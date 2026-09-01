# SyncBridge CRM Connector / WordPress CRM 连接器

This installable WordPress plugin forwards real enquiry submissions to a
self-hosted SyncBridge webhook. It is designed for property, services and other
lead-generation sites that need a controlled WordPress → CRM handoff without
changing their existing theme, XML listing import or portal export pipeline.

本插件把 WordPress 的真实咨询表单安全地发送到自托管 SyncBridge，适合房产、
服务预约等需要 WordPress → CRM 数据流的站点。插件独立于主题与现有 XML
房源导入/门户导出流程，不会改写现有数据链路。

## Install / 安装

### Upgrade to 0.1.1 / 升级提示

Version 0.1.1 prevents distinct enquiry IDs from collapsing after character
removal or case folding. Canonical lowercase ASCII keys (`a-z`, `0-9`, `_`, `-`,
1–200 bytes) stay unchanged, including `0`. Other nonempty keys use a reserved
`wp2:` SHA-256 namespace. Empty IDs still get a generated UUID. Pending WP-Cron
jobs keep their original wire key; retries do not normalize it again.

Before upgrading or rolling back, pause new submissions, drain existing retries
and keep the old delivery ledger. Do not replay already-submitted noncanonical
IDs across versions: their new keys differ and could create a second delivery.
If a replay is required, reconcile against the destination's business ID first.
Previously collapsed records cannot be recovered from a hash; reconcile them
with the original form records. This does not claim exactly-once CRM delivery.

0.1.1 修复不同询盘编号因删除字符或大小写折叠而被误判为同一条的问题。
规范小写 ASCII 键保持不变（包括 `0`）；其他非空键进入 `wp2:` SHA-256
命名空间，空键仍生成 UUID。已排队的 WP-Cron 重试继续沿用旧键。
升级或回滚前暂停新提交、处理完旧重试并保留投递台账；不要跨版本直接重放
已提交的非规范编号，否则新键可能造成第二次投递。确需重放时先按 CRM 业务
编号核对。历史误去重数据必须回查原表单记录，不能从哈希恢复。

The standalone PHP tests cover collisions, zero, length boundaries, Unicode,
reserved namespace separation and unchanged legacy retry keys. They use WordPress
function doubles, not a running WordPress/CRM integration acceptance environment.
PHP 独立测试覆盖碰撞、零值、长度、Unicode、命名空间和旧重试键；使用
WordPress 函数替身，不代表真实 WordPress→CRM 端到端验收。

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
