<?php

declare(strict_types=1);

define('ABSPATH', __DIR__);

$syncbridge_options = [
    'endpoint' => 'https://sync.example.test/webhooks/wordpress',
    'secret' => 'wordpress-secret-at-least-32-bytes',
    'field_map' => '{"name":"contact_name"}',
];
$syncbridge_requests = [];

function add_action(...$args): void {}
function add_settings_error(...$args): void {}
function add_settings_field(...$args): void {}
function add_settings_section(...$args): void {}
function add_options_page(...$args): void {}
function register_setting(...$args): void {}
function settings_fields(...$args): void {}
function do_settings_sections(...$args): void {}
function submit_button(...$args): void {}
function current_user_can(...$args): bool { return true; }
function esc_attr(string $value): string { return $value; }
function esc_textarea(string $value): string { return $value; }
function esc_url_raw(string $value): string { return $value; }
function sanitize_text_field(string $value): string { return trim($value); }
function sanitize_key(string $value): string
{
    return strtolower((string) preg_replace('/[^a-zA-Z0-9_-]/', '', $value));
}
function get_option(string $name, $default = null)
{
    global $syncbridge_options;
    return $syncbridge_options;
}
function home_url(string $path = ''): string { return 'https://wordpress.example.test' . $path; }
function wp_generate_uuid4(): string { return '00000000-0000-4000-8000-000000000000'; }
function wp_json_encode($value, int $flags = 0): string { return (string) json_encode($value, $flags); }
function wp_remote_post(string $url, array $args): array
{
    global $syncbridge_requests;
    $syncbridge_requests[] = ['url' => $url, 'args' => $args];
    return ['response' => ['code' => 202]];
}
function is_wp_error($value): bool { return false; }
function wp_remote_retrieve_response_code(array $response): int { return (int) $response['response']['code']; }
function wp_schedule_single_event(...$args): void {}
function do_action(...$args): void {}

require __DIR__ . '/../integrations/wordpress/syncbridge-crm/syncbridge-crm.php';

function submitted_key(string $input): string
{
    global $syncbridge_requests;
    $syncbridge_requests = [];
    SyncBridge_CRM_Connector::submit_enquiry(['name' => 'Ada'], $input);
    if (count($syncbridge_requests) !== 1) {
        throw new RuntimeException('Expected exactly one webhook request.');
    }
    return $syncbridge_requests[0]['args']['headers']['Idempotency-Key'];
}

$ordinary = submitted_key('Enquiry-42');
if ($ordinary !== 'enquiry-42') {
    throw new RuntimeException('Ordinary keys must retain their normalized value.');
}

foreach ([str_repeat('x', 201), '客户询盘编号'] as $unsafe) {
    $first = submitted_key($unsafe);
    $second = submitted_key($unsafe);
    if (!preg_match('/^[a-f0-9]{64}$/', $first) || $first !== $second) {
        throw new RuntimeException('Unsafe keys must become stable bounded SHA-256 values.');
    }
}

echo "WordPress connector boundary tests passed.\n";
