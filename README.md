# SyncBridge

Self-hosted webhook-to-Notion/REST synchronization with durable SQLite storage,
idempotency, retry backoff, a dead-letter state, authenticated metrics, and no
hosted-service dependency.

SyncBridge addresses recurring contract work involving CRM/ERP/Notion sync,
spreadsheet ingestion, webhook reliability and scheduled automation. It does not
ship invented customer records or claim an integration was tested without the
owner's real sandbox credentials.

## Run

```bash
cp .env.example .env
# edit secrets and destination
docker compose up --build
curl http://localhost:8080/health
```

Open `http://localhost:8080` for the operator console. Enter
`SYNCBRIDGE_API_TOKEN` to inspect delivery health, review recent events and retry
dead-letter deliveries. The token stays in the browser session only; event
payloads and destination credentials are never returned by the console API.

Native installation is also available:

```bash
./install.sh                 # Linux/macOS
.\install.ps1               # Windows PowerShell
.venv/bin/syncbridge init    # Linux/macOS secure setup; creates .env
.venv/bin/syncbridge import-csv customers.csv --map config/field-map.example.json
.venv/bin/syncbridge watch-csv ./incoming --interval 10
```

On Windows, replace `.venv/bin/syncbridge` with
`.\.venv\Scripts\syncbridge.exe`. The installer preserves an existing `.env`
and never creates a world-readable placeholder configuration.

Sign a webhook body with HMAC-SHA256 using `SYNCBRIDGE_WEBHOOK_SECRET`, then send:

```bash
curl -X POST http://localhost:8080/webhooks/crm \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: source-record-id' \
  -H 'X-SyncBridge-Signature: <hex hmac>' \
  --data-binary @record.json
```

The worker persists the event before delivery. Duplicate source/key pairs do not
create another event. Failed deliveries retry with bounded exponential backoff
and enter `dead` after five attempts.

`/health` reports the active storage backend (`sqlite` or `postgres`), the
selected destination adapter and whether its required configuration is present.
It queries the active database and returns HTTP 503 with `database_ready:false`
when that probe fails. This checks database access, not end-to-end delivery or
all worker failure modes. Queue acquisition failures retry after an interruptible
five-second wait without logging provider errors or connection strings. If an
outcome write fails, the worker retries that same database operation, not the
delivery. This also handles a lost acknowledgement after a successful commit.
The worker pauses acquisition while recording that outcome; monitor the structured
`queue_claim_failed` and `queue_outcome_write_failed` log events.

健康接口实际查询数据库，失败时返回 HTTP 503 与 `database_ready:false`。
这不代表目标 CRM 端到端验收，也不能检测全部工作线程故障。取队列失败后等待
五秒再尝试，等待可被停止信号打断；不记录数据库连接串或底层异常详情。
投递结果写入失败时，仅重试原数据库操作，不立即再次投递，也不继续领取新任务。
这覆盖提交成功后确认丢失的情况；应监控上述两类结构化错误日志。

Recovery boundary: a process crash after claiming a job, or shutdown during an
unresolved outcome write, can still leave a `processing` event requiring
operator reconciliation. Do not bulk replay uncertain deliveries: the destination
may already have accepted them. Lease-based recovery remains a release gate.
恢复边界：领取任务后崩溃、结果尚未确认写入时停止进程，仍可能留下 `processing`
事件，需人工核对目标系统是否已接收。不要批量重放不确定状态的投递；带租约的
恢复机制仍是待完成项。回滚可部署前一版本，不涉及数据库迁移；回滚后健康接口
将失去实际数据库探测，监控必须另行检查数据库。
It never returns credentials. This makes container and production checks detect
an accidentally unconfigured destination instead of reporting a misleading
SQLite-only status.

Set `NOTION_KEY_PROPERTY` to query and update an existing Notion page instead of
creating duplicates. Set `DATABASE_URL=postgresql://...` and install the
`postgres` extra for multi-worker deployments using `FOR UPDATE SKIP LOCKED`.

## WordPress CRM connector

### CSV integrity / CSV 数据完整性

CSV imports validate all records before the first queue write: empty/duplicate
column names, short/extra-column records, invalid UTF-8 and malformed quotes are
rejected without importing a valid prefix. Validation uses a temporary snapshot
that spills to disk above 1 MiB; allow disk space for the decoded file and secure
the host's temporary storage. Existing valid-file idempotency keys are unchanged.
Blank lines, UTF-8 BOM, quoted commas and multiline values remain supported.

This is format preflight, not an all-or-nothing database transaction. A database
failure during ingestion may leave a committed prefix; retry the unchanged file
at the same path with the same mapping/source to resume by deduplication. Do not
modify an input while it is being read; producers must write a temporary file and
atomically publish the finished `.csv`. Directory-watcher concurrency and archive
collisions remain unverified. Rollback needs no schema migration but restores
permissive parsing; keep the stricter input validation upstream if rolling back.

CSV 会在首次写队列前校验整份文件，拒绝空/重复列名、缺列/多列、非法 UTF-8
和错误引号，避免先导入前几行才发现坏行。临时快照超过 1 MiB 后落盘，部署时
须保留足够临时磁盘空间并保护临时目录。合法旧文件的幂等键保持不变，继续支持
BOM、空行、引号内逗号和多行文本。

这仅保证格式预检，不是整个导入的数据库事务；入库期间故障可能已提交部分行，
应在相同路径、映射和来源下重试原文件，利用去重续传。生产方必须先写临时文件，
完成后原子发布为 `.csv`，不能边写边导入。目录监听并发与归档重名仍待验证。
回滚无需迁移数据库，但会恢复宽松解析，须在上游保留严格校验。

An installable WordPress connector is included in
[`integrations/wordpress/syncbridge-crm`](integrations/wordpress/syncbridge-crm).
It captures Contact Form 7 or custom enquiry actions, sends only explicitly
mapped fields over an HTTPS/HMAC webhook, preserves a stable idempotency key and
retries transient failures with WP-Cron. This keeps an existing WordPress/XML
listing pipeline isolated while adding a durable CRM handoff.

仓库内含可安装的 WordPress CRM 连接器，支持 Contact Form 7 和自定义咨询 action。
它只发送明确映射的字段，要求 HTTPS 与 HMAC 验签，并用相同幂等键通过 WP-Cron
重试临时故障，因此可以在不改动现有主题、XML 导入或门户导出的情况下增加 CRM
数据同步。

## Security boundary

- Webhooks require an HMAC signature.
- Metrics require the operator bearer token.
- Destination credentials come only from environment variables.
- REST and Notion delivery refuse redirects (including same-host redirects).
  Configure the final canonical API URL; HTTP failures persist only the status,
  not redirect URLs or provider reason phrases. Existing bounded retry/dead-letter
  handling applies. Use HTTPS for external destinations; local HTTP is only for
  controlled development. To roll back, redeploy the previous image, but do not
  restore redirect-following delivery with real credentials.
- REST / Notion 外发请求拒绝重定向（包括同域跳转），防止授权令牌被转发。
  请配置最终 API 地址；HTTP 错误仅记录状态码，不保存跳转地址或服务商原因文本。
  失败继续走有限重试与死信流程。外部目标必须使用 HTTPS，本地 HTTP 仅供受控测试。
  回滚可重新部署前一镜像，但不要携带真实凭据恢复自动跟随跳转。
- The container runs as an unprivileged user with a read-only filesystem.
- Payloads may contain customer data; do not expose the SQLite volume publicly.

## Verification

```bash
python -m unittest discover -s tests -v
docker compose config
```

## 中文说明

SyncBridge 是可下载、自托管的业务数据同步工具，面向外包中反复出现的
CRM、ERP、Notion 和通用 API 同步需求。它先把 Webhook 数据持久化到
SQLite，再执行目标系统投递；支持幂等去重、指数退避重试、死信状态、
HMAC 验签和受保护监控指标。

启动后访问 `http://localhost:8080` 即可使用运维界面，查看投递状态、最近事件和
失败原因，并可手动重试死信任务。界面使用操作员令牌认证，不会返回事件正文或
目标系统凭据。

项目不包含虚构客户、订单或收入数据。Notion 端到端验证必须使用仓库所有者
提供的真实测试工作区凭据；未完成该验证前不会加入公开作品集。

## Roadmap / 下一里程碑

- Validate Notion create/update against an owner-provided test workspace
- Validate Docker Compose startup and outbound delivery in a deployed environment
