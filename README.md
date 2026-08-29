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
It never returns credentials. This makes container and production checks detect
an accidentally unconfigured destination instead of reporting a misleading
SQLite-only status.

Set `NOTION_KEY_PROPERTY` to query and update an existing Notion page instead of
creating duplicates. Set `DATABASE_URL=postgresql://...` and install the
`postgres` extra for multi-worker deployments using `FOR UPDATE SKIP LOCKED`.

## Security boundary

- Webhooks require an HMAC signature.
- Metrics require the operator bearer token.
- Destination credentials come only from environment variables.
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
