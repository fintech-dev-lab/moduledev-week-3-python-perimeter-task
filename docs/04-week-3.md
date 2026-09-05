# Неделя 3. Python-периметр, платёжный и ручной процессы

> Действующая редакция `week-3.1` заменяет прежний черновик receipt v1. Provider отправляет legacy callback без HMAC; Python adapter подписывает exact compact JSON bytes, а `providerPaymentId` всегда является непустой строкой.

> Provider принял запрос, ответ потерялся, callback пришёл раньше ожидания, dispatcher повторил отправку. Порядок сообщений не является гарантией.

## Цель

Использовать общий C# action runtime и workflow-ядро для двух предметных процессов, а интеграционный периметр реализовать отдельными Python-процессами. PostgreSQL остаётся авторитетным хранилищем и единственным местом принятия предметных решений.

## Срок и сдача

Работа выполняется с 4 по 10 сентября 2026 года. Дедлайн — 10 сентября, 23:59 МСК.

Проверяется продолжение того же репозитория и полный SHA commit. До дедлайна участник отправляет куратору URL, ветку и SHA и заранее предоставляет доступ. В сдачу не включаются `.env`, реальные секреты, build output, логи и сгенерированные отчёты проверки.

## Проверяемый результат

После чистой сборки проверяющий контур:

1. Останавливает Python dispatcher и доказывает, что внешний запрос сохранён в Outbox до HTTP-вызова.
2. Запускает dispatcher и проводит `PAYMENT_EXECUTION` через выданный provider-simulator и Python receipt adapter.
3. Доставляет ранний, повторный и конфликтующий legacy callback и проверяет Inbox.
4. Имитирует потерю ответа provider и подтверждает `paymentCount = 1` через provider audit.
5. Проводит автоматическую и ручную ветви `PAYMENT_APPROVAL`.
6. Проверяет, что три Python-процесса не владеют предметным состоянием и работают только через фиксированные HTTP/SQL-границы.
7. Пересоздаёт Python-процессы и продолжает обработку по состоянию PostgreSQL.

`payment-processing` завершается только после принятой и применённой квитанции. `payment-review` завершается только по серверному правилу или аудированному ручному решению. Повторы не создают второй процесс, внешний платёж, переход или решение.

## Продолжение недель 1 и 2

Не меняются следующие границы:

- `gateway`, `api`, `worker-a` и `worker-b` остаются C#-процессами;
- automatic step вызывает зарегистрированный PostgreSQL action через shared executor и `api.invoke`;
- в workflow-карте сохраняется `service = postgres`;
- новый action не требует отдельного controller/handler;
- ядро не ветвится по имени flow, step или action;
- PostgreSQL хранит operations, process state, history, Outbox, Inbox, receipts и decisions.

Неделя 3 добавляет интеграционный слой, но не переносит в Python выбор карты, лимит, финальный статус или workflow transitions.

## Целевая архитектура

```text
PostgreSQL Outbox
  -> Python outbox-dispatcher
  -> provider-simulator v0.2.0
  -> Python receipt-adapter
  -> gateway
  -> generic C# API
  -> receipt.accept PostgreSQL action
  -> Inbox
  -> Python inbox-reconciler
  -> persisted workflow signal
  -> generic C# worker
  -> final PostgreSQL action
```

Provider-simulator v0.2.0 является выданным внешним компонентом на Go. Участник пишет Python anti-corruption layer, который изолирует legacy provider contract от внутреннего versioned receipt contract. Это исправляет расхождение wire formats, а не маскирует его.

## Обязательный Compose seam

Решение продолжает adapter недели 2 и добавляет сервисы:

| Сервис | Технология и граница |
|---|---|
| `gateway` | C#, единственная внешняя точка на host-порту `8080` |
| `api` | C#, generic action runtime, без host-порта |
| `cli` | Course CLI как container entrypoint |
| `postgres` | PostgreSQL, база `course`, named volume |
| `worker-a`, `worker-b` | Один C# worker image, без host-портов |
| `outbox-dispatcher` | Локально собранный Python image, PostgreSQL -> provider |
| `receipt-adapter` | Тот же Python image, provider callback -> gateway |
| `inbox-reconciler` | Тот же Python image, фиксированная SQL reconciliation boundary |
| `provider-simulator` | Выданный image v0.2.0 по закреплённому digest |

Три Python-сервиса запускаются разными entrypoints одного локально собранного image. Используется Python 3.12 или новее. Framework, HTTP-клиент, PostgreSQL driver и структура package остаются выбором участника.

Проверка не оценивает количество Python-кода и имена модулей. Она проверяет локальную сборку image, Python runtime процессов, разные полномочия и наблюдаемое поведение.

Python-сервисы и provider не публикуют host-порты. Bind mounts исходников, Docker socket, host network, privileged mode и внешние Compose resources запрещены по правилам adapter недели 2.

## Конфигурация периметра

Обязательные имена переменных:

| Переменная | Получатель | Назначение |
|---|---|---|
| `PROVIDER_URL` | `outbox-dispatcher` | `http://provider-simulator:8081` |
| `PROVIDER_CALLBACK_CAPABILITY` | `receipt-adapter`, provider callback URL | Непредсказуемый сегмент callback path |
| `PROVIDER_CALLBACK_TOKEN` | `receipt-adapter` | JWT principal `receipt-provider` со scope `receipt:write` |
| `PROVIDER_HMAC_SECRET` | `receipt-adapter`, `api` | Ключ подписи нормализованного body |
| `RECEIPT_API_URL` | `receipt-adapter` | `http://gateway:8080/api/receipt/accept` |
| `OUTBOX_OWNER` | `outbox-dispatcher` | Стабильный owner `outbox-dispatcher` |
| `COURSE_TEST_PROFILE` | Все проверяемые процессы | Короткие интервалы публичной/скрытой проверки |

Полная таблица, включая PostgreSQL credentials, опубликована в [конфигурационном контракте](configuration.md). Synthetic JWT/HMAC/capability values передаются только через environment проверочного запуска и не имеют default в tracked Compose. Документированные local-only placeholders разрешены только для паролей PostgreSQL; checker всегда заменяет их. Реальные secrets не попадают в repository, image layers, stdout, structured logs, trace и report.

## Python `outbox-dispatcher`

Dispatcher не читает физические таблицы и не вызывает `api.invoke`. Его роль `outbox_dispatcher` имеет `EXECUTE` только на функции:

```sql
delivery.claim_outbox(
  p_owner text,
  p_limit integer
) returns table (
  outbox_id uuid,
  lease_version bigint,
  external_request_id text,
  correlation_id uuid,
  amount text,
  currency text
)

delivery.succeed_outbox(
  p_outbox_id uuid,
  p_owner text,
  p_lease_version bigint,
  p_provider_payment_id text
) returns jsonb

delivery.fail_outbox(
  p_outbox_id uuid,
  p_owner text,
  p_lease_version bigint,
  p_error_code text
) returns jsonb
```

Один claim соответствует ровно одной HTTP-попытке. Dispatcher отправляет:

```http
POST /payments
Idempotency-Key: <externalRequestId>
X-Correlation-ID: <correlationId>
Content-Type: application/json
```

```json
{
  "operationId": "<externalRequestId>",
  "amount": "1000.00",
  "currency": "RUB"
}
```

Legacy-поле `operationId` намеренно содержит `externalRequestId`: provider требует равенство body field и `Idempotency-Key`. Каждая повторная попытка использует те же key, body и correlation identifier.

Успешное принятие имеет HTTP `202` и строгое тело `{"providerPaymentId":"...","status":"ACCEPTED"}`. Полная классификация ответов и error codes опубликована во [внешнем контракте](external-contracts.md).

На неделе 3 PostgreSQL рассчитывает `next_attempt_at` и допускает ограниченные повторы для transport error, timeout, HTTP 408, 429 и 5xx. Остальные 4xx являются non-retryable. `succeed_outbox` и `fail_outbox` условно проверяют owner и `lease_version` и не могут вернуть `CONFIRMED` delivery в более раннее состояние. Несколько dispatcher, jitter и окончательный `DEAD` доводятся на неделе 4.

## Выданный provider v0.2.0

Используется image:

```text
ghcr.io/fintech-dev-lab/internship-provider-simulator:v0.2.0
sha256:70e5e0dd9ab8425be84de431ec74516f9bedf5d5529077358e2e2b2037fe0c74
```

Provider принимает идемпотентный `POST /payments` и отправляет legacy callback без JWT и HMAC:

```json
{
  "providerPaymentId": "provider-123",
  "operationId": "external-123",
  "result": "COMPLETED",
  "message": "Payment completed",
  "occurredAt": "2026-09-04T12:00:00Z"
}
```

`result` равен `COMPLETED` или `REJECTED`. Provider поддерживает режимы `success`, `reject`, `delay`, `duplicate`, `lost-response`, `early-callback`, `conflicting-callback` и `transient-error`. Его память не переживает recreate; проверка не предъявляет такого требования к внешнему simulator.

## Python `receipt-adapter`

Adapter слушает внутри Compose:

```http
POST /callbacks/provider-v02/{capability}
```

Неверный capability возвращает `404`. Adapter не имеет PostgreSQL credentials и не хранит авторитетное состояние.

Он строго валидирует legacy body, запрещает неизвестные поля и CR/LF в строковых транспортных полях, сохраняет исходную строку `occurredAt` и переводит callback в receipt v1:

| Provider v0.2.0 | Receipt v1 |
|---|---|
| `providerPaymentId` | `messageId` |
| `operationId` | `externalRequestId` |
| `result` | `outcome` |
| `providerPaymentId` | `providerPaymentId` |
| исходная строка `occurredAt` | исходная строка `occurredAt` |
| `message` | проверяется по типу/размеру и отбрасывается |

Использование `providerPaymentId` как `messageId` делает duplicate и conflicting callback одного provider payment одной дедупликационной областью.

Нормализованный body:

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

Adapter сериализует его UTF-8 вызовом, эквивалентным:

```python
json.dumps(receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
```

Без BOM и завершающего LF. HMAC-SHA256 считается над точными HTTP body bytes. Заголовок:

```text
X-Provider-Signature: v1=<lowercase-hex-hmac-sha256>
```

Ключом являются точные UTF-8 bytes `PROVIDER_HMAC_SECRET` без trim, Base64 или hex decoding. Adapter отправляет body через generic gateway с `Authorization: Bearer <PROVIDER_CALLBACK_TOKEN>`, `X-Action-Version: 1`, `Idempotency-Key: <messageId>` и подписью.

HMAC доказывает целостность участка adapter -> platform. Provider v0.2.0 сам подпись не создаёт. Доверие к его callback в учебном контуре ограничено изолированной Compose network и capability URL; это не модель полного банковского криптографического периметра.

## Generic signature boundary

`api` проверяет `X-Provider-Signature` над исходными body bytes до предметного вызова и сравнивает decoded bytes constant-time. Проверка общая для подписанных запросов и не ветвится по имени action.

При успехе C# добавляет в server-side context:

```json
{
  "transport": {
    "signatureVerified": true,
    "signatureVersion": 1
  }
}
```

В context не попадают secret и полная signature. Неверный формат, версия или HMAC возвращают `401 signature.invalid`, target не вызывается. Отсутствие подписи не ломает остальные actions, но `receipt.accept` требует `transport.signatureVerified = true` и иначе возвращает `403 receipt.signature_required` без Inbox mutation.

## `receipt.accept` и Inbox

Action имеет policy `receipt:write`, idempotency mode `required` и outcomes `RECEIVED`, `DUPLICATE`.

Новый receipt:

- проверяет version, trusted signature marker и известный `externalRequestId`;
- сохраняет Inbox с глобально уникальным `messageId` и SHA-256 exact body bytes;
- сохраняет receipt и связь с operation/process;
- переводит связанную Outbox delivery в `CONFIRMED`, не регрессируя её state;
- возвращает после durable commit, даже если workflow signal ещё не применён.

Идентичный `messageId` и body hash возвращают исходный результат. Тот же `messageId` с другим body даёт `409 idempotency.conflict`; исходная запись, signal и финальный статус не меняются.

Успешный result содержит `messageId`, `externalRequestId` и сохранённый `state`. Нормативные HTTP statuses и error codes опубликованы во [внешнем контракте](external-contracts.md).

Receipt может прийти до `WAITING_SIGNAL`. Переход в wait проверяет уже сохранённый Inbox, поэтому раннее сообщение не теряется.

## Python `inbox-reconciler`

Reconciler не читает и не изменяет физические таблицы напрямую. Роль `inbox_reconciler` имеет `EXECUTE` только на:

```sql
delivery.reconcile_inbox(p_limit integer) returns integer
```

Один вызов атомарно применяет подходящие `RECEIVED` сообщения к pinned process, создаёт/дедуплицирует workflow signal и переводит Inbox в `APPLIED`. Неподходящее раннее сообщение остаётся `RECEIVED`. Вход соответствующего `wait_signal` также проверяет Inbox в своей транзакции.

В test profile poll interval не более 500 ms. Recreate reconciler не теряет работу: очередь определяется только состоянием PostgreSQL.

## Обязательная граница `payment.submit`

Payload version 1:

```json
{
  "operationId": "8c26513d-8441-43ea-b064-3bca8c240052"
}
```

Action требует `payment:write` и `Idempotency-Key`, не принимает flow name/version, operation kind, amount, limit или final status.

Одна транзакция:

```text
operation CREATED -> PROCESSING
+ process instance pinned to active flow version
+ first step/job or waiting state
+ OPERATION_SUBMITTED event
+ idempotency result
```

Серверная таблица binding сопоставляет:

| `operationKind` | Flow |
|---|---|
| `PAYMENT_EXECUTION` | `payment-processing` |
| `PAYMENT_APPROVAL` | `payment-review` |

Идентичный repeat возвращает исходный subject result команды со `status=PROCESSING` и существующий pinned process, в том числе после завершения operation или смены default flow version. Актуальный operation status читается отдельно. Changed body с тем же key даёт conflict. Нельзя получить operation `PROCESSING` без process или process для operation `CREATED`.

## Процесс `payment-processing`, версия 1

```text
validate_operation
  -> prepare_external_request
  -> wait_receipt
  -> apply_receipt
  -> [COMPLETED] complete_operation
  -> [REJECTED] reject_operation
  -> end
```

Automatic steps вызывают через общий runtime:

- `payment.validate`;
- `payment.prepare_external`;
- `payment.apply_receipt`;
- `payment.complete`;
- `payment.reject`.

`payment.prepare_external` одной transaction создаёт `external_request`, Outbox и завершает workflow job. Все retries job используют один `executionId`, поэтому создаётся один external request.

`payment.apply_receipt` читает только сохранённый receipt. Клиентский body и provider transport response не могут напрямую установить final status.

## Процесс `payment-review`, версия 1

```text
validate_operation
  -> check_limit
  -> [WITHIN_LIMIT] approve_operation
  -> [REVIEW_REQUIRED] wait_manual_decision
  -> [APPROVED] approve_operation
  -> [REJECTED] reject_operation
  -> end
```

Automatic steps вызывают `payment.validate`, `payment.check_limit`, `payment.approve` и `payment.reject`.

В test/course profile действует server-side rule `course-limit-v1`: сумма до `100000.00 RUB` включительно даёт `WITHIN_LIMIT`, сумма выше даёт `REVIEW_REQUIRED`. Клиент не передаёт limit или rule version.

Автоматическое решение сохраняется с `source=LIMIT_RULE` и `rule_version=course-limit-v1`.

## Ручное решение

Action `workflow.manual` version 1 требует `workflow:manual` и `Idempotency-Key`. Payload:

```json
{
  "processId": "21a34f72-83cd-4e68-9cf2-a07ab20034fa",
  "stepInstanceId": "bdab3a76-3473-4c79-a7c5-6ff743658eda",
  "decision": "APPROVED",
  "reason": "Документы проверены"
}
```

`decision` равен `APPROVED` или `REJECTED`; `reason` содержит 1-500 символов и не является idempotency key.

- Решение допустимо только для текущего `manual` step pinned process.
- Principal берётся из trusted context, а не payload.
- Decision, workflow event и следующий job фиксируются одной transaction.
- Identical repeat возвращает те же `status`, `outcome` и `result`; transport headers и `meta` могут отличаться.
- Changed или concurrent decision возвращает conflict.
- Decision view хранит source `MANUAL`, principal, reason hash, outcome и время.

## Обязательные actions

Реализуйте и зарегистрируйте PostgreSQL actions:

- `payment.submit`;
- `operation.events`;
- `payment.validate`;
- `payment.prepare_external`;
- `payment.apply_receipt`;
- `payment.complete`;
- `payment.reject`;
- `payment.check_limit`;
- `payment.approve`;
- `receipt.accept`;
- `workflow.manual`.

Все automatic actions имеют явную version и вызываются worker через shared executor. `receipt.accept` и `workflow.manual` публикуются тем же generic HTTP route, а не отдельными controllers.

## Проверочные проекции

Сохраняются views недель 1 и 2 и добавляются:

| View | Обязательные колонки |
|---|---|
| `external_requests` | `external_request_id text`, `operation_id uuid`, `state text`, `payload_hash text`, `created_at timestamptz` |
| `receipts` | `message_id text`, `external_request_id text`, `message_version integer`, `outcome text`, `signature_valid boolean`, `body_hash text`, `received_at timestamptz`, `applied_at timestamptz` |
| `outbox` | `outbox_id uuid`, `external_request_id text`, `state text`, `attempt_count integer`, `next_attempt_at timestamptz`, `last_error_code text`, `created_at timestamptz`, `delivered_at timestamptz` |
| `inbox` | `message_id text`, `body_hash text`, `state text`, `received_at timestamptz`, `applied_at timestamptz` |
| `decisions` | `decision_id uuid`, `process_id uuid`, `step_instance_id uuid`, `source text`, `principal text`, `reason_hash text`, `outcome text`, `rule_version text`, `created_at timestamptz` |

States соответствуют `07-autocheck-outline.md`. Views не раскрывают capability, JWT, HMAC secret/signature, callback `message`, полный body или reason.

## Практическое задание «Python-периметр: сеть не читала вашу диаграмму»

Реализуйте:

- migrations, actions и server-side bindings двух процессов;
- карты `payment-processing` и `payment-review` версии 1;
- `external_request`, Outbox, Inbox, receipts и decisions;
- один Python package/image и три обязательных entrypoints;
- dispatcher с фиксированной least-privilege SQL boundary;
- legacy callback adapter и receipt v1;
- generic signed-body boundary C# API без action-specific controller;
- Inbox reconciler через одну фиксированную PostgreSQL function;
- manual decision boundary;
- end-to-end tests provider и ручной ветви;
- Python unit/contract tests serialization, HMAC, callback mapping и HTTP classification;
- обновлённые C4 Container и ADR о trust boundary/anti-corruption layer.

## Открытая проверка

Публикационный пакет `moduledev-week-3-python-perimeter-task` содержит Python checker, JSON Schemas, score manifest и фиксированные public fixtures.

```bash
./check.sh --repo /path/to/participant-solution
```

Checker выполняет cold build, создаёт изолированный Compose project, подменяет только synthetic secrets и test intervals, запускает success/duplicate/conflict/manual scenarios и пишет `week-3-public-report.json` без баллов и секретов.

## Критерии приёмки

- Три integration services работают на Python 3.12+ из одного локально собранного image.
- API/gateway/worker остаются C#, provider image закреплён по digest.
- Python не выбирает flow, limit, transition или final status и не хранит авторитетное состояние.
- Остановленный dispatcher оставляет durable Outbox; после запуска доставка продолжается.
- Один external request и provider payment сохраняются при worker/dispatcher retries и lost response.
- Valid adapter receipt даёт `COMPLETED` или `REJECTED` по сохранённому outcome.
- Invalid signature/version/capability/externalRequestId не меняют Inbox, process и operation.
- Early receipt сохраняется и применяется после готовности wait step.
- Duplicate receipt возвращает исходный result; conflicting body даёт conflict.
- Recreate adapter/reconciler не теряет callback, сохранённый в PostgreSQL.
- Automatic limit decision и manual decision имеют обязательный audit.
- Concurrent submit и manual decision создают один принятый effect.
- `payment-processing` и `payment-review` выполняются без специальных C# веток.
- Роли `outbox_dispatcher` и `inbox_reconciler` не имеют direct table DML и лишних functions.
- Логи, reports и images не содержат synthetic secrets и full messages.

## Скрытые проверки

Hidden checker использует другие identifiers, суммы и interleavings, меняет provider modes, заменяет URL на trusted capture service и проверяет exact outbound bytes/headers. Он дополнительно проверяет детерминированный callback до `WAITING_SIGNAL`, malformed legacy JSON, unknown fields, CR/LF, timestamp precision, HTTP 408/429/4xx/5xx classification, callback retry after adapter outage, stale delivery completion, 20 concurrent submit/manual requests и неизменность C# API/worker image digests.

Скрытая проверка проверяет опубликованные инварианты, а не имена Python modules, framework или ORM.

## Оценка недели

Неделя добавляет 25 баллов:

| ID | Баллы | Инвариант |
|---|---:|---|
| `BNK-01` | 3 | Request создаёт одну operation, submit — один pinned process |
| `BNK-02` | 3 | `payment-processing` завершает operation только по применённому receipt |
| `BNK-03` | 3 | Provider, Python adapter, signed receipt и Inbox соблюдают trust boundary |
| `BNK-04` | 3 | `payment-review` проходит auto/manual branches общим ядром |
| `BNK-05` | 3 | Final result подтверждён receipt или audited decision |
| `PG-02` | 5 | Action effect, finish job, state и event атомарны |
| `PG-05` | 5 | Outbox/Inbox не теряют early/repeated messages и не создают второй effect |
| **Итого** | **25** | |

## Стоп-факторы

К сдаче недели и итоговому рейтингу применяется единый перечень из `06-rating-and-checks.md`. Этот документ не переопределяет его формулировки.

## Артефакты недели

- работающий commit продолжения недели 2;
- две опубликованные и активированные карты;
- PostgreSQL actions, migrations и least-privilege roles;
- локально собранный Python integration image с тремя entrypoints;
- provider client, legacy adapter, receipt v1 и reconciler;
- JSON Schemas provider/receipt/submit/manual contracts;
- Python unit/contract tests и end-to-end tests;
- stable `autocheck` views;
- C4 Container update и ADR о trust boundary;
- README с секциями `Архитектура`, `Запуск`, `Python-периметр`, `Provider`, `Проверка`, `Диагностика`, `Ограничения`.

## Не входит в неделю

- перенос API, generic workflow worker или authoritative state в Python;
- отдельные C# handlers для payment/receipt/manual actions;
- новый provider вместо выданного v0.2.0;
- distributed transaction с provider;
- несколько dispatcher/reconciler, jitter, окончательный dead-letter и production secret manager;
- BPMN import, произвольные expressions и branches по имени flow;
- обещание exactly-once.

Эксплуатационная конкуренция, backoff/jitter, `DEAD`, health/metrics и failpoint-run доводятся на неделе 4.
