# Контур автоматической проверки

## Назначение

Автопроверка оценивает решение как чёрный ящик через опубликованный API, документированный CLI, provider-simulator и стабильные read-only представления PostgreSQL. Она не зависит от имён C# классов, ORM и внутреннего расположения файлов.

Контур должен доказать не только HTTP-ответ, но и предметное состояние, неизменяемую историю, отсутствие дублей и восстановление после сбоя.

## Общий интерфейс решения

Чистый запуск:

```bash
docker compose up -d --build
```

Открытая проверка:

```bash
./check.sh
```

Для отдельного публикационного пакета недели 3 команда принимает репозиторий решения явно: `./check.sh --repo /path/to/participant-solution`.

Для Windows допустим эквивалент:

```powershell
.\check.ps1
```

Команды, обязательные переменные окружения и таймаут готовности описываются в README. После запуска не требуется ручная настройка БД, публикация actions или изменение конфигурации.

Для недели 1 публикационный пакет `moduledev-week-1-gateway-task` использует test adapter с Compose services `gateway`, `api`, `cli`, `postgres` и базой `course`. Отдельный C# gateway является единственной внешней точкой на host-порту `8080`; C# API доступен только внутри Compose. Открытая команда остаётся `./check.sh`. Проверяющий контур не исполняет host scripts сдачи: команды migration/action передаются entrypoint сервиса `cli`, а доверенные fixtures монтируются read-only.

API принимает синтетическую HS256-конфигурацию через `COURSE_JWT_ISSUER`, `COURSE_JWT_AUDIENCE`, `COURSE_JWT_SIGNING_KEY`. Закрытый прогон генерирует новый key и новые module/action/schema/function/property/outcome после C# build. Имена service и базы являются только стабильным проверочным seam; внутренние таблицы и C# структура не фиксируются.

Для недели 2 adapter добавляет C# services `worker-a` и `worker-b` из одного image без host-портов. Lease owners равны именам services. Checker фиксирует digests API/worker images до hidden publication и не пересобирает их. Test-only `flow test-finish` доступна только при `COURSE_TEST_PROFILE=1`, вызывает production fencing boundary и не даёт прямой DML или произвольный SQL target.

Для недели 3 adapter добавляет participant-built services `outbox-dispatcher`, `receipt-adapter`, `inbox-reconciler` и выданный `provider-simulator`. Первые три используют один локально собранный Python 3.12+ image с разными entrypoints и без host-портов. Checker проверяет Python runtime, общий image digest, неизменность C# API/worker images и отсутствие авторитетного состояния вне PostgreSQL.

## Вступительное задание: внешний provider

Вступительное задание проверяется отдельно от четырёхнедельного курса. Контур запускает решение кандидата с опубликованным симулятором:

```text
ghcr.io/fintech-dev-lab/internship-provider-simulator:v0.2.0
```

Для воспроизводимости скрытая проверка закрепляет manifest digest:

```text
sha256:70e5e0dd9ab8425be84de431ec74516f9bedf5d5529077358e2e2b2037fe0c74
```

Открытый сценарий вступительного задания:

1. Дождаться `GET /health` решения и provider-simulator.
2. Создать операцию.
3. Выполнить `submit` и проверить `PROCESSING`.
4. Подтвердить по аудиту provider единственный `POST /payments` с `Idempotency-Key = operationId`.
5. Дождаться callback на `POST /receipts`.
6. Проверить `COMPLETED`, providerPaymentId, историю и корреляцию.
7. Пересоздать сервис без удаления volume и повторно проверить состояние.

Скрытая проверка использует `reject`, `delay`, `duplicate`, `lost-response`, `early-callback`, `conflicting-callback`, `transient-error`, конкурентный `submit` и рестарт после фактического принятия платежа. Для каждой операции аудит provider должен показывать `paymentCount = 1`.

Результат вступительной проверки бинарный: `зачёт` или `незачёт`. Ошибка решения, provider и проверочной инфраструктуры классифицируются раздельно.

## Интерфейс адаптационного курса

### Action CLI

```text
./course.sh action validate <manifest>
./course.sh action publish <manifest>
./course.sh action list
./course.sh action activate <module.action> --version <version>
./course.sh action disable <module.action> --version <version> [--replacement-version <version>]
./course.sh migration apply <directory>
```

### Workflow CLI

```text
./course.sh flow validate <file>
./course.sh flow publish <file>
./course.sh flow list
./course.sh flow activate <flow> --version <version>
./course.sh flow start <flow> --business-key <key> [--data <file>]
./course.sh flow get <process-id>
./course.sh flow signal <process-id> --type <type> --message-id <id> --payload <file>
./course.sh flow test-finish <job-id> --owner <owner> --lease-version <version> --outcome <outcome> --result <file>
```

Команды возвращают ненулевой exit code при ошибке и машиночитаемый JSON в stdout. Скрытая проверка применяет доверенную migration с новой PostgreSQL-функцией, затем публикует её manifest и карту через CLI, а не прямой DML.

`flow test-finish` не является обычной административной командой: вне test profile она недоступна. Stale owner/version возвращает non-zero exit code и `workflow.lease_stale`.

`migration apply` доступна только локальному учебному/проверочному контуру, принимает каталог `.sql`, выполняет файлы в лексикографическом порядке одной транзакцией на файл и сохраняет checksum применённых migrations. Команда запускается ролью публикации; API и worker не имеют её credentials. Повтор с тем же checksum безопасен, изменение уже применённого файла возвращает conflict.

CLI пишет в stdout ровно один JSON-документ; диагностический текст допускается только в stderr. Общий envelope:

```json
{
  "status": "ok",
  "result": {
    "resource": "action",
    "operation": "published",
    "key": "payment.request",
    "version": 1
  },
  "meta": {
    "contractVersion": "course-1"
  }
}
```

При ошибке `status=error`, обязательны `code`, `message` и `meta.contractVersion`; exit code ненулевой. `validate` не меняет данные, повторный идентичный `publish` возвращает исходный результат, а конфликтующая публикация отклоняется.

Операционные action-команды не меняют contract revision. `activate` атомарно включает выбранную версию, делает её default и снимает default с прежней. `disable` требует `--replacement-version`, если после отключения останутся другие включённые версии route.

Для flow-команд поле `result` имеет следующие обязательные формы:

| Команда | Обязательные поля `result` |
|---|---|
| `flow publish` | `resource="flow"`, `operation="published"`, `flowName`, `flowVersion` |
| `flow activate` | `resource="flow"`, `operation="activated"`, `flowName`, `flowVersion` |
| `flow start` | `resource="process"`, `operation="started"`, `processId`, `flowName`, `flowVersion`, `state` |
| `flow get` | `resource="process"`, `processId`, `flowName`, `flowVersion`, `state`, `currentStepKey` |
| `flow signal` | `resource="signal"`, `processId`, `messageId`, `signalType`, `status` со значением `accepted` или `duplicate` |

`processId` сериализуется UUID-строкой. List-команды возвращают `result.items` как массив объектов тех же ресурсов.

`flow publish` сохраняет неизменяемую версию и не активирует её. `flow activate` одной транзакцией выбирает ровно одну опубликованную active version для новых экземпляров. Запущенные процессы навсегда сохраняют свою `flowVersion`. `flow start` идемпотентен по `flow_name/business_key`: same data возвращают прежний instance, changed data дают conflict.

Локальная доверенная команда `flow signal` предназначена для проверки workflow-ядра недели 2 и пишет `workflow_signal` напрямую. Она не создаёт запись `autocheck.inbox`. Внешняя квитанция недели 3 сначала фиксируется в Inbox, затем атомарно или через согласователь создаёт/применяет workflow signal.

### Action manifest `course-1`

Starter kit содержит machine schema `contracts/course-1/action-manifest.schema.json`. Wire format использует snake_case и запрещает неизвестные поля:

```json
{
  "contract_version": "course-1",
  "module": "training",
  "action": "canary",
  "version": 1,
  "http_method": "POST",
  "target_schema": "training",
  "target_function": "canary_v1",
  "request_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["value"],
    "properties": {"value": {"type": "string"}},
    "additionalProperties": false
  },
  "response_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["stored"],
    "properties": {"stored": {"type": "boolean"}},
    "additionalProperties": false
  },
  "outcomes": ["APPLIED"],
  "required_policy": ["workflow:execute"],
  "idempotency_mode": "required",
  "idempotency_scope": "principal_action",
  "timeout_ms": 2000,
  "enabled": true,
  "is_default": true
}
```

`http_method` принимает только `POST`. `version` положительна; `timeout_ms` находится в диапазоне 1–30000. `outcomes` — непустой уникальный массив uppercase identifiers. `required_policy` — массив scopes, все элементы которого обязательны. `idempotency_mode`: `none`, `optional` или `required`; scope: `none`, `principal_action`, `consumer_action` или `global_action`. `response_schema` валидирует только `result` успешного envelope. `is_default=true` требует `enabled=true`.

### Workflow map `course-1`

Starter kit содержит machine schema `contracts/course-1/workflow-map.schema.json`. CLI принимает эквивалентные JSON или YAML. Минимальный YAML:

```yaml
contract_version: course-1
flow_name: workflow-smoke
version: 1
start_step: invoke_canary
steps:
  - key: invoke_canary
    type: automatic
    task:
      service: postgres
      module: training
      action: canary
      action_version: 1
      required_policy: [workflow:execute]
      timeout_ms: 2000
      retry:
        max_attempts: 3
        delays_ms: [200, 400]
      input_mapping:
        /value: /value
      input_constants: {}
  - key: wait_result
    type: wait_signal
    signal_type: training.completed
    outcome: RECEIVED
  - key: done
    type: end
    outcome: COMPLETED
transitions:
  - from: invoke_canary
    outcome: APPLIED
    to: wait_result
  - from: wait_result
    outcome: RECEIVED
    to: done
```

`input_mapping` сопоставляет target JSON Pointer action payload с source JSON Pointer в process data по RFC 6901; произвольные expressions и код запрещены. Target mappings не пересекаются между собой и с `input_constants`. Missing source даёт non-retryable `workflow.mapping_missing`, action не вызывается. У `manual` обязательны уникальные `allowed_outcomes`; у `wait_signal` — `signal_type` и фиксированный `outcome`; у `end` — финальный `outcome`. Все action/manual outcomes должны иметь ровно один transition.

`task.required_policy` как множество точно совпадает с `required_policy` action version, а server-side scopes `workflow-worker` содержат это множество. `retry.max_attempts` включает первую attempt; `delays_ms` содержит ровно `max_attempts - 1` значений. Повторяются `retryable=true` и runtime timeout; mapping/contract violations non-retryable. На неделе 2 `manual` исполняется до persisted `WAITING_MANUAL`; публичное завершение manual step относится к неделе 3.

### HTTP API

Проверка использует:

- `POST /api/{module}/{action}`;
- `X-Action-Version`;
- Bearer JWT учебного issuer;
- `Idempotency-Key`;
- `GET /health/live`;
- `GET /health/ready`;
- `GET /metrics`;
- `GET /openapi/default.json`;
- `GET /openapi/actions/{module}/{action}/{version}.json`.

Для signed requests API также принимает `X-Provider-Signature`. Неверная signature возвращает `401 signature.invalid`; отсутствие signature у `receipt.accept` возвращает `403 receipt.signature_required`. Остальные actions не обязаны быть подписаны.

`default.json` содержит только default-версии включённых routes. Versioned document содержит одну точную operation без объединения версий через `oneOf`.

### Provider курса

Курс повторно использует выданный образ вступительного задания:

```text
ghcr.io/fintech-dev-lab/internship-provider-simulator:v0.2.0
sha256:70e5e0dd9ab8425be84de431ec74516f9bedf5d5529077358e2e2b2037fe0c74
```

Compose участника подключает image по digest. Provider v0.2.0 реализован на Go и сохраняет legacy wire contract вступительного задания. Он принимает `POST /payments`, где `Idempotency-Key` совпадает с body `operationId`, и отправляет callback:

```json
{
  "providerPaymentId": "provider-123",
  "operationId": "external-123",
  "result": "COMPLETED",
  "message": "Payment completed",
  "occurredAt": "2026-09-04T12:00:00Z"
}
```

Участник реализует Python anti-corruption layer. `receipt-adapter` переводит legacy callback в receipt v1: `messageId=providerPaymentId`, `externalRequestId=operationId`, `outcome=result`, сохраняет исходную строку `occurredAt` и отбрасывает `message` после валидации.

Нормализованный receipt сериализуется compact UTF-8 JSON с `sort_keys=true`, без BOM и завершающего LF. `X-Provider-Signature: v1=<lowercase-hex-hmac-sha256>` считается над точными HTTP body bytes. Generic C# boundary проверяет signature до target и передаёт в trusted context только `transport.signatureVerified` и `transport.signatureVersion`. HMAC защищает участок adapter -> platform; provider v0.2.0 сам сообщения не подписывает.

### Python integration boundary

`outbox-dispatcher` использует роль `outbox_dispatcher` и только функции:

```text
delivery.claim_outbox(text, integer)
delivery.succeed_outbox(uuid, text, bigint, text)
delivery.fail_outbox(uuid, text, bigint, text)
```

`inbox-reconciler` использует роль `inbox_reconciler` и только `delivery.reconcile_inbox(integer)`. Обе роли не имеют direct table DML, migration privileges, `api.invoke` или workflow finish functions. `receipt-adapter` не имеет PostgreSQL credentials.

Один Outbox claim создаёт ровно одну provider HTTP attempt. Все attempts сохраняют `externalRequestId`, body и correlation identifier. HTTP 408, 429, 5xx, timeout и transport error retryable; остальные 4xx non-retryable. PostgreSQL определяет state и `next_attempt_at`.

Adapter принимает legacy callback только на `/callbacks/provider-v02/{capability}`, подписывает receipt v1 и вызывает generic `POST /api/receipt/accept` с JWT principal `receipt-provider`, `Idempotency-Key=messageId` и `X-Action-Version: 1`. Wrong capability даёт 404. Invalid signature/version не достигают target. `receipt.accept` сохраняет Inbox до ответа; Python reconciler применяет сохранённое сообщение через фиксированную SQL boundary.

### Проверочные проекции

Starter kit фиксирует версию `course-1` read-only schema `autocheck`. Все identifiers имеют тип `uuid`, если в таблице не указан `text`; время имеет тип `timestamptz` UTC, версии — `integer`, `lease_version` — `bigint`, хеши — lowercase hex `text`.

| View | Обязательные колонки |
|---|---|
| `autocheck.contract_info` | `contract_version text`, `generated_at timestamptz` |
| `autocheck.action_definitions` | `module text`, `action text`, `version integer`, `http_method text`, `target_schema text`, `target_function text`, `outcomes jsonb`, `enabled boolean`, `is_default boolean` |
| `autocheck.action_dispatches` | `correlation_id`, `request_id text`, `module text`, `action text`, `version integer`, `principal text`, `payload_hash text`, `status text`, `outcome text`, `occurred_at` |
| `autocheck.operations` | `operation_id`, `request_id text`, `operation_kind text`, `amount numeric`, `currency text`, `status text`, `process_id`, `created_at`, `updated_at` |
| `autocheck.operation_events` | `event_id`, `operation_id`, `event_type text`, `payload_hash text`, `occurred_at` |
| `autocheck.flow_versions` | `flow_name text`, `flow_version integer`, `status text`, `is_active boolean`, `published_at` |
| `autocheck.processes` | `process_id`, `business_key text`, `flow_name text`, `flow_version integer`, `state text`, `current_step_key text`, `created_at`, `updated_at` |
| `autocheck.steps` | `step_instance_id`, `process_id`, `step_key text`, `step_type text`, `state text`, `outcome text`, `entered_at`, `completed_at` |
| `autocheck.jobs` | `job_id`, `process_id`, `step_instance_id`, `execution_id`, `state text`, `lease_owner text`, `lease_version bigint`, `lease_until`, `attempt_count integer`, `next_attempt_at` |
| `autocheck.attempts` | `attempt_id`, `job_id`, `execution_id`, `lease_version bigint`, `attempt_number integer`, `status text`, `outcome text`, `error_code text`, `started_at`, `finished_at` |
| `autocheck.signals` | `message_id text`, `process_id`, `signal_type text`, `body_hash text`, `status text`, `received_at` |
| `autocheck.workflow_events` | `event_id`, `process_id`, `step_instance_id`, `event_type text`, `occurred_at` |
| `autocheck.external_requests` | `external_request_id text`, `operation_id`, `state text`, `payload_hash text`, `created_at` |
| `autocheck.receipts` | `message_id text`, `external_request_id text`, `message_version integer`, `outcome text`, `signature_valid boolean`, `body_hash text`, `received_at`, `applied_at` |
| `autocheck.outbox` | `outbox_id`, `external_request_id text`, `state text`, `attempt_count integer`, `next_attempt_at`, `last_error_code text`, `created_at`, `delivered_at` |
| `autocheck.inbox` | `message_id text`, `body_hash text`, `state text`, `received_at`, `applied_at` |
| `autocheck.decisions` | `decision_id`, `process_id`, `step_instance_id`, `source text`, `principal text`, `reason_hash text`, `outcome text`, `rule_version text`, `created_at` |

Nullability следует опубликованной модели состояния: например, `process_id` отсутствует до `submit`, lease-поля пусты у незахваченного job, `outcome` пуст у незавершённого или ошибочного шага. `contract_info` содержит ровно одну строку. Проекции не раскрывают secrets, signature и полный payload. Физические таблицы и ORM участник выбирает самостоятельно; проверяющая роль имеет только `SELECT` на schema `autocheck`.

Все enum-подобные значения в views используют uppercase ASCII:

| Поле | Допустимые значения |
|---|---|
| `action_dispatches.status` | `OK`, `ERROR` |
| `operations.status` | `CREATED`, `PROCESSING`, `COMPLETED`, `REJECTED` |
| `flow_versions.status` | `PUBLISHED` |
| `processes.state` | `CREATED`, `RUNNING`, `WAITING_SIGNAL`, `WAITING_MANUAL`, `COMPLETED`, `FAILED` |
| `steps.step_type` | `AUTOMATIC`, `WAIT_SIGNAL`, `MANUAL`, `END` |
| `steps.state` | `PENDING`, `READY`, `RUNNING`, `WAITING`, `COMPLETED`, `FAILED` |
| `jobs.state` | `READY`, `LEASED`, `RETRY_WAIT`, `SUCCEEDED`, `DEAD` |
| `attempts.status` | `RUNNING`, `SUCCEEDED`, `FAILED`, `STALE` |
| `signals.status` | `ACCEPTED`, `APPLIED`, `CONFLICT` |
| `external_requests.state` | `CREATED`, `SENT`, `CONFIRMED` |
| `receipts.outcome` | `COMPLETED`, `REJECTED` |
| `outbox.state` | `PENDING`, `LEASED`, `RETRY_WAIT`, `DELIVERED`, `DEAD`, `CONFIRMED` |
| `inbox.state` | `RECEIVED`, `APPLIED`, `CONFLICT` |
| `decisions.source` | `LIMIT_RULE`, `MANUAL` |
| `decisions.outcome` | `APPROVED`, `REJECTED` |

Поля `outcome` actions и steps используют значения из соответствующего manifest/map. `workflow_events.event_type` является исключением из uppercase-правила и использует PascalCase domain event names, например `SignalReceived`, `ProcessCompleted` и `TaskFailed`. Error codes используют lowercase dotted identifiers из опубликованного error contract.

## Этапы проверки курса

| Этап | Проверяемый результат |
|---|---|
| 1. Сборка | Все images собираются без ручных действий |
| 2. Запуск | Контейнеры стартуют на чистом окружении |
| 3. Готовность | `live` и `ready` отражают фактическое состояние |
| 4. Контракт | OpenAPI и реальное поведение согласованы |
| 5. Оркестратор | Manifest публикует функцию как endpoint; target/schema/policy соблюдаются |
| 6. Workflow | Версии, шаги, переходы, jobs, attempts и ожидание сохранены |
| 7. Предметный результат | Operation, event и обязательное свидетельство согласованы |
| 8. Идемпотентность | Повторы не создают дополнительных эффектов |
| 9. Конкуренция | Инварианты сохраняются при нескольких клиентах и worker |
| 10. Восстановление | Restart и expired lease не теряют работу |
| 11. Outbox/Inbox | Сообщения не теряются и применяются не более одного раза |
| 12. Наблюдаемость | Путь собирается по одному известному идентификатору |
| 13. Безопасность | Нет секретов, SQL target и лишних данных в журналах |

## Стоп-факторы

Автопроверка использует единый перечень и точные формулировки из `06-rating-and-checks.md`. Стоп-фактор фиксируется отдельно от баллов, попадает в `stopFactors` отчёта и исключает решение из итогового топ-10 до устранения.

## Проверка по неделям

### Неделя 1

1. Собрать и запустить контур.
2. Применить доверенную migration со скрытой PostgreSQL-функцией.
3. Опубликовать её manifest через action CLI.
4. Вызвать action по generic endpoint без пересборки API.
5. Проверить default и explicit version.
6. Проверить неизвестный/disabled action и недостаточную policy.
7. Проверить request/response schema и неизвестный outcome.
8. Вернуть error после изменения canary-строки и проверить rollback.
9. Отправить одинаковые и конфликтующие idempotent requests.
10. Выполнить конкурентный `payment.request`.
11. Сверить OpenAPI, operation, event и dispatch log.
12. После `SET ROLE course_runtime` проверить запрет `UPDATE` operation и `DELETE` event через read-only проекции.

### Неделя 2

1. Выполнить sandbox preflight, cold build/up и regression smoke недели 1.
2. После build сгенерировать и опубликовать hidden action с новыми именами/schema/outcomes.
3. Проверить valid/invalid maps без side effects, затем publish/activate hidden v1.
4. Зафиксировать неизменные API/worker image digests и запустить process через CLI.
5. Пройти automatic → `WAITING_SIGNAL` → end; отдельно достичь persisted `WAITING_MANUAL`.
6. Проверить accepted/duplicate/conflicting signal и append-only state/history.
7. Запустить два worker и доказать один предметный эффект одного logical job.
8. По acknowledgement `after_job_claim` остановить owner, дождаться reclaim и роста `leaseVersion`.
9. Через test adapter отклонить finish со старым owner/`leaseVersion`.
10. По acknowledgement `after_action_before_finish` доказать rollback и один effect после retry.
11. Проверить bounded retries, `DEAD`, `FAILED` и `TaskFailed`.
12. Опубликовать/активировать v2, сравнить pinned old/new instances и idempotent start.
13. Пересоздать worker containers и проверить READY, RETRY_WAIT, WAITING_SIGNAL, WAITING_MANUAL.
14. Проверить запрет direct DML роли `workflow_worker` и safe logs/errors.

### Неделя 3

1. Выполнить regression smoke недель 1-2 и зафиксировать C# API/worker image digests.
2. Проверить Compose seams и один Python image для dispatcher, adapter и reconciler.
3. Опубликовать и активировать версии 1 карт `payment-processing` и `payment-review`.
4. Остановить dispatcher, идемпотентно создать/submit `PAYMENT_EXECUTION`, проверить durable Outbox и отсутствие provider audit record.
5. Запустить dispatcher, проверить exact provider request и один process/external request/payment.
6. Проверить перевод legacy callback в signed receipt v1 и успешную/отказную ветви.
7. Доставить legacy callback до `WAITING_SIGNAL`, затем identical duplicate и conflicting body одного `messageId`.
8. Проверить wrong capability, invalid signature/version/externalRequestId и отсутствие mutation.
9. Имитировать `lost-response` и `transient-error`; provider audit должен показать `paymentCount=1`.
10. Пересоздать Python services и проверить продолжение Outbox/Inbox по PostgreSQL state.
11. Запустить `PAYMENT_APPROVAL` ниже/выше `100000.00 RUB`, проверить `course-limit-v1`.
12. Параллельно выполнить manual decisions и проверить audit.
13. Проверить least-privilege Python roles и отсутствие DB credentials у adapter.
14. Проверить, что обе карты исполнялись без специальных веток ядра и C# image digests не изменились.

### Неделя 4

1. Запустить несколько worker и Outbox dispatcher.
2. Сделать provider недоступным и проверить pending/retry state.
3. Восстановить provider и проверить продолжение доставки.
4. Исчерпать retries и проверить `DEAD` без ложного process failure.
5. Доставить позднюю квитанцию после `DEAD` Outbox.
6. Активировать failpoint на каждой согласованной границе.
7. Остановить и перезапустить затронутый контейнер.
8. Повторить callback и manual decision после полного рестарта.
9. Сверить health, metrics, trace и read-only проекции.
10. Проверить логи и images на секреты.

## Открытые сценарии

Участник заранее получает тесты:

- чистого запуска;
- публикации нового PostgreSQL action без изменения C#;
- выполнения новой карты без изменения worker;
- отклонения неизвестного action и invalid payload;
- идемпотентного request/submit;
- линейной карты и ожидания signal;
- успешной и отказной квитанции;
- неверной подписи;
- ручного решения;
- immutable history и trace;
- сохранения данных после рестарта;
- доставки Outbox после восстановления provider в пределах retry policy;
- Python callback mapping, exact signed bytes и Inbox reconciliation после recreate.

## Скрытые сценарии

Скрытая проверка меняет значения, порядок, параллельность и точки отказа:

- 20–100 конкурентных одинаковых команд;
- один idempotency key с разными данными;
- попытка передать БД, schema, function и SQL через route/payload;
- disabled action, несовместимая версия и недостаточная policy;
- новый action key и новая карта после сборки C#;
- неизвестные service/action/version в task definition;
- попытка worker вызвать action без required policy;
- canary action с изменением и error envelope;
- result, не соответствующий response schema;
- несколько worker и dispatcher;
- lease expiration и stale completion;
- падение после claim и после предметного изменения;
- early, duplicate и conflicting receipt;
- один `messageId` с разным телом;
- invalid HMAC и message version;
- malformed legacy callback, wrong capability и подмена adapter target;
- exact compact JSON bytes, timestamp preservation и HMAC over raw body;
- Python processes из разных/prebuilt images или с лишними PostgreSQL privileges;
- конкурентные manual decisions;
- provider unavailable и lost response;
- рестарт API, worker и dispatcher на границах commit;
- граничные decimal values;
- неизвестные поля и версии;
- отсутствие или подмена correlation metadata;
- секреты и полный payload в коде, images или logs.

Скрытый сценарий проверяет тот же опубликованный инвариант, что и открытый, но использует другие данные и interleaving.

## Детерминированные failpoints

Failpoints включаются только в закрытом test profile и недоступны из публичного API. Контур задаёт компоненту `COURSE_TEST_PROFILE=1` и `COURSE_FAILPOINT=<name>` через Compose override до его запуска.

Минимальные точки:

| `COURSE_FAILPOINT` | Компонент | Граница |
|---|---|---|
| `after_job_claim` | worker | после commit аренды до action |
| `after_action_before_finish` | worker | после action effect и contract validation внутри transaction до `finish_job` и commit |
| `after_outbox_claim` | Python dispatcher | после commit claim до provider HTTP |
| `after_provider_response` | Python dispatcher | после provider response до conditional delivery completion |
| `after_inbox_saved` | API/Python reconciler | после Inbox commit до workflow signal |
| `after_manual_decision` | API/worker | после фиксации решения до следующего job |

При достижении точки компонент пишет одну JSON-запись `{"event":"failpoint.reached","name":"after_job_claim","instanceId":"..."}` и блокируется до принудительной остановки. Проверка ждёт эту запись, останавливает компонент, удаляет failpoint из override, запускает компонент и проверяет инварианты. Потеря ответа после фактического принятия provider включается документированным режимом `lost-response` самого simulator. Случайные `sleep` вместо acknowledgement barrier не используются.

## Тестовый профиль

| Параметр | Значение |
|---|---:|
| Lease job | 2 секунды |
| Poll interval worker | не более 100 мс |
| Provider timeout | 500 мс |
| Максимум попыток Outbox | 3 |
| Задержки Outbox | 200, 400, 800 мс |
| Inbox reconciliation | не более 500 мс |
| Provider callback retry | 200 мс |
| Timeout скрытого сценария | 30 секунд |

Все интервалы задаются конфигурацией. Обычный профиль может использовать более консервативные значения без изменения семантики.

## Изоляция

- Каждый сценарий использует собственные identifiers.
- Сценарии не зависят от порядка запуска.
- Окружение можно удалить и развернуть заново.
- Проверка не изменяет исходный код участника.
- Скрытые assets монтируются отдельно и не попадают в репозиторий.
- Таймауты и retries ограничены.
- Зависший компонент завершается с отдельным infrastructure/error code.

## Формат отчёта

```json
{
  "manifestVersion": "course-1",
  "total": 100,
  "earned": 92,
  "stopFactors": [],
  "sections": {
    "orchestrator": 15,
    "workflow": 20,
    "postgresql": 20,
    "banking": 15,
    "reliability": 12,
    "observability": 10
  },
  "criteria": [
    {
      "id": "WFL-04",
      "earned": 4,
      "status": "passed",
      "diagnosticCode": null
    }
  ]
}
```

Сумма maximum section weights равна 100. Этот JSON является внутренним machine artifact и не раскрывает точные скрытые payload, credentials и failpoint sequence.

Для недели 2 используется score manifest `week-2.1`: maximum 25, cumulative maximum 45, criteria `WFL-01` ... `WFL-05` и `PG-03`. Criterion получает полный вес либо 0, но хранит атомарные evidence items со статусами `passed`, `failed`, `unverified`. Обязательны формулы `earned + failedPoints + unverifiedPoints = 25` и `coveragePoints = earned + failedPoints`. Доказанные admission failures хранятся отдельно и дают verdict `FAILED`; environment/infrastructure blockers не превращаются в failed evidence.

Для недели 3 используется score manifest `week-3.1`: maximum 25, cumulative maximum 70, criteria `BNK-01` ... `BNK-05`, `PG-02`, `PG-05`. Manifest фиксирует Python runtime/image admission, provider legacy mapping, signed receipt bytes, Outbox/Inbox evidence, manual branches и least-privilege probes. Public report не содержит баллов; числовой scorecard остаётся внутренним artifact.

Week 2 scorecard остаётся `provisional` до завершения runtime, trusted static review и integrity validation. Provisional artifact нельзя публиковать или передавать PDF generator. Final artifact привязан к commit SHA, fixture digest и API/worker image digests.

После автоматического прогона и ручной статической проверки формируются два PDF:

- внутренний PDF для куратора содержит диагностический балл, вклад в рейтинг курса, доказательства, стоп-факторы, failed checks, блокеры и рекомендацию;
- внешний PDF для стажёра содержит подтверждённые области и необходимые исправления без criterion IDs, failed check names, diagnostic codes и hidden-данных.

Для недели 1 machine artifact использует самостоятельную диагностическую шкалу из 100 и отдельно хранит вклад до 20 баллов в общий рейтинг курса. Оба значения переносятся только во внутренний PDF куратора. Для недели 2 внутренний PDF содержит новые 25 баллов и накопительный максимум 45; для недели 3 — новые 25 и накопительный максимум 70. Внешний PDF не содержит числовую оценку, internal IDs и hidden-данные.

## Что не проверяется

- промпты и история общения с AI;
- конкретный агентский инструмент;
- число агентов, skills или MCP-серверов;
- стиль работы участника;
- устные объяснения;
- количество строк кода;
- субъективная архитектурная сложность.
