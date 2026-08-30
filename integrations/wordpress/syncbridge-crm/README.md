# SyncBridge CRM Connector / WordPress CRM 连接器

This installable WordPress plugin forwards real enquiry submissions to a
self-hosted SyncBridge webhook. It is designed for property, services and other
lead-generation sites that need a controlled WordPress → CRM handoff without
changing their existing theme, XML listing import or portal export pipeline.

本插件把 WordPress 的真实咨询表单安全地发送到自托管 SyncBridge，适合房产、
服务预约等需要 WordPress → CRM 数据流的站点。插件独立于主题与现有 XML
房源导入/门户导出流程，不会改写现有数据链路。

## Install / 安装

1. Copy `syncbridge-crm` into `wp-content/plugins/` and activate it.
2. Open **Settings → SyncBridge CRM**.
3. Enter an HTTPS endpoint such as
   `https://sync.example.com/webhooks/wordpress`.
4. Enter the same 32+ character secret used as
   `SYNCBRIDGE_WEBHOOK_SECRET` by SyncBridge.
5. Map only the form fields the destination is allowed to receive:

```json
{
  "your-name": "contact_name",
  "your-email": "contact_email",
  "property-reference": "property_reference",
  "preferences": "property_preferences",
  "budget": "budget"
}
```

插件只发送映射中明确列出的字段。Webhook 必须使用 HTTPS，密钥至少 32 位。

## Supported submissions / 支持的提交方式

- Contact Form 7 is captured automatically after activation.
- Custom plugins can invoke the public WordPress action:

```php
do_action(
    'syncbridge_crm_submit_enquiry',
    [
        'name' => $name,
        'email' => $email,
        'property_reference' => $property_reference,
        'budget' => $budget,
    ],
    'enquiry-' . $enquiry_id
);
```

自定义插件可以调用同名 action。第二个参数应使用来源系统中稳定、唯一的记录
编号，避免网络重试产生重复 CRM 记录。

## Delivery and failure path / 投递与失败路径

- Each request is HMAC-SHA256 signed and carries an idempotency key.
- A 2xx response means SyncBridge durably accepted the event.
- Network/non-2xx failures are retried by WP-Cron up to five times with bounded
  exponential backoff and the same idempotency key.
- After the final failure, `syncbridge_crm_delivery_failed` is emitted with the
  key and HTTP status; payload contents are not logged.
- SyncBridge then handles destination retries and its own dead-letter queue.

每次请求均使用 HMAC-SHA256 验签和幂等键。网络错误或非 2xx 响应通过 WP-Cron
最多重试五次；最终失败只触发状态事件，不写入客户表单内容。SyncBridge 接收后
继续负责目标 CRM 的重试与死信处理。

## Acceptance check / 验收检查

1. Submit one real staging enquiry with a unique enquiry ID.
2. Confirm WordPress receives HTTP 202/200 from SyncBridge.
3. Confirm the event appears once in the authenticated SyncBridge console.
4. Confirm mapped fields arrive in the CRM staging record.
5. Temporarily block the endpoint and confirm WP-Cron schedules a retry.
6. Restore the endpoint and confirm the same idempotency key creates no duplicate.

Production CRM verification requires the owner-provided WordPress staging site,
form names, SyncBridge URL and CRM sandbox credentials. No test result is claimed
without those credentials.

