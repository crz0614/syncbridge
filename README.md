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

项目不包含虚构客户、订单或收入数据。Notion 端到端验证必须使用仓库所有者
提供的真实测试工作区凭据；未完成该验证前不会加入公开作品集。

## Roadmap / 下一里程碑

- CSV directory watcher and field-map configuration
- Notion schema discovery and update/upsert support
- PostgreSQL backend for multi-worker deployments
- Signed release archives and reproducible installers
