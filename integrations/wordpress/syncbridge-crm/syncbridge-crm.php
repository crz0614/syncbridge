<?php
/**
 * Plugin Name: SyncBridge CRM Connector
 * Description: Reliably forwards WordPress enquiries to a self-hosted SyncBridge endpoint.
 * Version: 0.1.0
 * Requires at least: 6.4
 * Requires PHP: 8.1
 * License: MIT
 */

if (!defined('ABSPATH')) {
    exit;
}

final class SyncBridge_CRM_Connector
{
    private const OPTION = 'syncbridge_crm_options';
    private const RETRY_HOOK = 'syncbridge_crm_retry_delivery';
    private const MAX_ATTEMPTS = 5;

    public static function boot(): void
    {
        add_action('admin_init', [self::class, 'register_settings']);
        add_action('admin_menu', [self::class, 'add_settings_page']);
        add_action('syncbridge_crm_submit_enquiry', [self::class, 'submit_enquiry'], 10, 2);
        add_action(self::RETRY_HOOK, [self::class, 'retry_delivery'], 10, 1);
        add_action('wpcf7_before_send_mail', [self::class, 'capture_contact_form_7']);
    }

    public static function register_settings(): void
    {
        register_setting('syncbridge_crm', self::OPTION, [
            'type' => 'array',
            'sanitize_callback' => [self::class, 'sanitize_options'],
            'default' => [],
        ]);

        add_settings_section('syncbridge_crm_connection', 'Connection', '__return_false', 'syncbridge-crm');
        foreach ([
            'endpoint' => ['SyncBridge webhook URL', 'url'],
            'secret' => ['Webhook secret', 'password'],
            'field_map' => ['Field map (JSON)', 'textarea'],
        ] as $key => [$label, $type]) {
            add_settings_field(
                'syncbridge_crm_' . $key,
                $label,
                [self::class, 'render_field'],
                'syncbridge-crm',
                'syncbridge_crm_connection',
                ['key' => $key, 'type' => $type]
            );
        }
    }

    public static function sanitize_options($input): array
    {
        $current = get_option(self::OPTION, []);
        $endpoint = isset($input['endpoint']) ? esc_url_raw(trim((string) $input['endpoint'])) : '';
        if ($endpoint !== '' && !str_starts_with($endpoint, 'https://')) {
            add_settings_error(self::OPTION, 'https_required', 'The webhook URL must use HTTPS.');
            $endpoint = (string) ($current['endpoint'] ?? '');
        }

        $secret = trim((string) ($input['secret'] ?? ''));
        if ($secret === '') {
            $secret = (string) ($current['secret'] ?? '');
        } elseif (strlen($secret) < 32) {
            add_settings_error(self::OPTION, 'secret_too_short', 'The webhook secret must be at least 32 characters.');
            $secret = (string) ($current['secret'] ?? '');
        }

        $raw_map = trim((string) ($input['field_map'] ?? ''));
        $decoded = json_decode($raw_map, true);
        if (!is_array($decoded) || !self::valid_field_map($decoded)) {
            add_settings_error(self::OPTION, 'invalid_map', 'Field map must be a JSON object of source-to-destination strings.');
            $raw_map = (string) ($current['field_map'] ?? '{}');
        }

        return ['endpoint' => $endpoint, 'secret' => $secret, 'field_map' => $raw_map ?: '{}'];
    }

    private static function valid_field_map(array $map): bool
    {
        foreach ($map as $source => $destination) {
            if (!is_string($source) || !is_string($destination) || $source === '' || $destination === '') {
                return false;
            }
        }
        return true;
    }

    public static function add_settings_page(): void
    {
        add_options_page(
            'SyncBridge CRM',
            'SyncBridge CRM',
            'manage_options',
            'syncbridge-crm',
            [self::class, 'render_settings_page']
        );
    }

    public static function render_field(array $args): void
    {
        $options = get_option(self::OPTION, []);
        $key = $args['key'];
        $value = (string) ($options[$key] ?? ($key === 'field_map' ? '{}' : ''));
        $name = self::OPTION . '[' . $key . ']';
        if ($args['type'] === 'textarea') {
            printf('<textarea class="large-text code" rows="8" name="%s">%s</textarea>', esc_attr($name), esc_textarea($value));
            echo '<p class="description">Example: {"your-name":"contact_name","your-email":"contact_email","budget":"budget"}</p>';
            return;
        }
        printf(
            '<input class="regular-text" type="%s" name="%s" value="%s" autocomplete="%s">',
            esc_attr($args['type']),
            esc_attr($name),
            esc_attr($value),
            $args['type'] === 'password' ? 'new-password' : 'off'
        );
    }

    public static function render_settings_page(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }
        echo '<div class="wrap"><h1>SyncBridge CRM</h1>';
        echo '<p>Forwards only explicitly mapped form fields. A failed request is retried by WP-Cron with the same idempotency key.</p>';
        echo '<form action="options.php" method="post">';
        settings_fields('syncbridge_crm');
        do_settings_sections('syncbridge-crm');
        submit_button();
        echo '</form></div>';
    }

    public static function capture_contact_form_7($contact_form): void
    {
        if (!class_exists('WPCF7_Submission')) {
            return;
        }
        $submission = WPCF7_Submission::get_instance();
        if (!$submission) {
            return;
        }
        $posted = $submission->get_posted_data();
        $id = method_exists($contact_form, 'id') ? (string) $contact_form->id() : 'unknown';
        self::submit_enquiry($posted, 'cf7-' . $id . '-' . wp_generate_uuid4());
    }

    public static function submit_enquiry($raw, $idempotency_key = ''): void
    {
        if (!is_array($raw)) {
            return;
        }
        $options = get_option(self::OPTION, []);
        $map = json_decode((string) ($options['field_map'] ?? '{}'), true);
        if (!is_array($map) || !self::valid_field_map($map)) {
            return;
        }

        $payload = [];
        foreach ($map as $source => $destination) {
            if (!array_key_exists($source, $raw)) {
                continue;
            }
            $value = is_array($raw[$source]) ? implode(', ', array_map('sanitize_text_field', $raw[$source])) : sanitize_text_field((string) $raw[$source]);
            $payload[$destination] = $value;
        }
        if ($payload === []) {
            return;
        }
        $payload['_source'] = ['site' => home_url('/'), 'integration' => 'wordpress'];
        $job = [
            'payload' => $payload,
            'idempotency_key' => sanitize_key((string) ($idempotency_key ?: wp_generate_uuid4())),
            'attempt' => 1,
        ];
        self::deliver_or_schedule($job, $options);
    }

    private static function deliver_or_schedule(array $job, ?array $options = null): void
    {
        $options = $options ?? get_option(self::OPTION, []);
        $endpoint = (string) ($options['endpoint'] ?? '');
        $secret = (string) ($options['secret'] ?? '');
        if ($endpoint === '' || $secret === '') {
            return;
        }
        $body = wp_json_encode($job['payload'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        $response = wp_remote_post($endpoint, [
            'timeout' => 10,
            'blocking' => true,
            'headers' => [
                'Content-Type' => 'application/json',
                'Idempotency-Key' => $job['idempotency_key'],
                'X-SyncBridge-Signature' => hash_hmac('sha256', $body, $secret),
            ],
            'body' => $body,
        ]);
        $status = is_wp_error($response) ? 0 : (int) wp_remote_retrieve_response_code($response);
        if ($status >= 200 && $status < 300) {
            return;
        }

        $attempt = (int) $job['attempt'];
        if ($attempt >= self::MAX_ATTEMPTS) {
            do_action('syncbridge_crm_delivery_failed', $job['idempotency_key'], $status);
            return;
        }
        $job['attempt'] = $attempt + 1;
        $delay = min(3600, 60 * (2 ** ($attempt - 1)));
        wp_schedule_single_event(time() + $delay, self::RETRY_HOOK, [$job]);
    }

    public static function retry_delivery(array $job): void
    {
        if (!isset($job['payload'], $job['idempotency_key'], $job['attempt']) || !is_array($job['payload'])) {
            return;
        }
        self::deliver_or_schedule($job);
    }
}

SyncBridge_CRM_Connector::boot();

