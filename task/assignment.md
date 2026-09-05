# Зона 3. Python-периметр

## Результат

Продолжите репозиторий недели 2. Реализуйте два предметных flow и интеграционный периметр:

```text
Outbox -> Python dispatcher -> provider v0.2.0
provider legacy callback -> Python adapter -> generic C# API -> Inbox
Inbox -> Python reconciler -> workflow signal -> generic C# worker
```

PostgreSQL хранит operations, process state, history, Outbox, Inbox, receipts и decisions. Python не выбирает flow, limit, transition или final status.

## Обязательные сервисы

К seam недели 2 добавляются:

| Service | Контракт |
|---|---|
| `outbox-dispatcher` | Python 3.12+, PostgreSQL -> provider |
| `receipt-adapter` | Тот же Python image, provider callback -> gateway |
| `inbox-reconciler` | Тот же Python image, fixed reconciliation function |
| `provider-simulator` | Выданный v0.2.0 image по digest |

Python services не имеют host-портов. Adapter не имеет database credentials. Dispatcher/reconciler используют разные least-privilege roles без direct table DML.

## Provider compatibility

Точные HTTP status, error codes и правила повторов определены в [публичном внешнем контракте](../docs/external-contracts.md).

Provider принимает body:

```json
{"operationId":"<externalRequestId>","amount":"1000.00","currency":"RUB"}
```

`Idempotency-Key` равен `externalRequestId`. Provider callback v0.2.0:

```json
{
  "providerPaymentId": "provider-123",
  "operationId": "external-123",
  "result": "COMPLETED",
  "message": "Payment completed",
  "occurredAt": "2026-09-04T12:00:00Z"
}
```

Adapter преобразует его в receipt v1:

```json
{
  "externalRequestId": "external-123",
  "messageId": "provider-123",
  "occurredAt": "2026-09-04T12:00:00Z",
  "outcome": "COMPLETED",
  "providerPaymentId": "provider-123",
  "version": 1
}
```

Receipt сериализуется compact JSON с sorted keys. HMAC-SHA256 считается над точными UTF-8 body bytes. Adapter вызывает `POST /api/receipt/accept` через gateway с JWT, `Idempotency-Key=messageId`, `X-Action-Version: 1` и `X-Provider-Signature: v1=<lowercase hex>`.

Generic C# signature boundary передаёт target только trusted markers `transport.signatureVerified` и `transport.signatureVersion`. Invalid signature не достигает target.

## SQL boundaries Python

```text
delivery.claim_outbox(text, integer)
delivery.succeed_outbox(uuid, text, bigint, text)
delivery.fail_outbox(uuid, text, bigint, text)
delivery.reconcile_inbox(integer)
```

Один claim создаёт одну HTTP attempt. Retry сохраняет key/body/correlation. PostgreSQL определяет state и next attempt. Conditional finish не регрессирует `CONFIRMED` delivery.

## Предметные contracts

`payment.submit` принимает только `operationId` и Idempotency-Key. Binding хранится server-side:

- `PAYMENT_EXECUTION` -> `payment-processing`;
- `PAYMENT_APPROVAL` -> `payment-review`.

`payment-processing`:

```text
validate -> prepare_external -> wait_receipt -> apply_receipt
  -> COMPLETED: complete -> end
  -> REJECTED: reject -> end
```

`payment-review`:

```text
validate -> check_limit
  -> WITHIN_LIMIT: approve -> end
  -> REVIEW_REQUIRED: manual
       -> APPROVED: approve -> end
       -> REJECTED: reject -> end
```

Course rule `course-limit-v1`: до `100000.00 RUB` включительно — auto approve, выше — manual.

`workflow.manual` принимает process/step, decision и reason. Idempotency key берётся из HTTP header, principal — из trusted context. Decision, event и next job атомарны.

## Обязательные actions

- `payment.submit`, `operation.events`;
- `payment.validate`, `payment.prepare_external`, `payment.apply_receipt`;
- `payment.complete`, `payment.reject`;
- `payment.check_limit`, `payment.approve`;
- `receipt.accept`, `workflow.manual`.

## Acceptance

- Один local Python image, три Python entrypoints.
- C# API/worker images не меняются из-за flow/action names.
- Остановленный dispatcher оставляет durable Outbox.
- Lost response не создаёт второй provider payment.
- Early receipt не теряется.
- Duplicate возвращает исходный result; conflicting callback даёт conflict.
- Invalid capability/signature/version/externalRequestId не меняют state.
- Recreate Python services продолжает работу по PostgreSQL state.
- Auto/manual decisions имеют audit.
- Direct DML integration roles запрещён.
- Secrets/full messages отсутствуют в repo, images, logs и report.

Действующая редакция `week-3.1`, полный контракт, configuration seam и контур проверки опубликованы в [docs](../docs/README.md) этого репозитория. Эта редакция явно заменяет прежний черновик receipt v1.
