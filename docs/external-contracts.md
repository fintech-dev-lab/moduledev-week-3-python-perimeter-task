# Внешние контракты недели 3

Этот документ фиксирует значения, которые checker проверяет буквально. JSON-тела должны соответствовать [опубликованным schemas](../contracts/course-1).

## Dispatcher -> provider

Dispatcher отправляет:

```http
POST {PROVIDER_URL}/payments
Content-Type: application/json
Idempotency-Key: <externalRequestId>
X-Correlation-ID: <correlationId>
```

```json
{"operationId":"<externalRequestId>","amount":"1000.00","currency":"RUB"}
```

Успешное принятие имеет HTTP `202` и строгое JSON-тело без дополнительных полей:

```json
{"providerPaymentId":"provider-123","status":"ACCEPTED"}
```

Классификация результата попытки:

| Результат | Действие dispatcher | `p_error_code` для `delivery.fail_outbox` |
|---|---|---|
| `202` и корректное тело | `delivery.succeed_outbox` | Не применяется |
| Transport error или timeout | Повтор разрешён PostgreSQL policy | `transport.error.retryable` |
| HTTP `408`, `429`, `5xx` | Повтор разрешён PostgreSQL policy | `http.<status>.retryable` |
| Прочий HTTP status | Повтор запрещён | `http.<status>.terminal` |
| `202` с некорректным UTF-8/JSON/телом | Повтор запрещён | `response.invalid.terminal` |

Redirect не считается успешным `202`. Каждый повтор одного delivery сохраняет exact body, `Idempotency-Key` и `X-Correlation-ID`. Главный инвариант при lost response: provider audit показывает `paymentCount = 1`; число HTTP attempts может быть больше одного.

## Provider -> adapter

Единственный callback route:

```http
POST /callbacks/provider-v02/{PROVIDER_CALLBACK_CAPABILITY}
Content-Type: application/json
```

Тело соответствует `provider-v02-callback.schema.json`. Adapter ограничивает body размером 64 KiB, строго отвергает unknown fields и CR/LF в строковых транспортных полях.

| Ситуация | Ответ adapter |
|---|---|
| Неверный path или capability | `404`, пустое тело |
| Некорректный Content-Length, JSON или callback schema | `400`, пустое тело |
| Generic API ответил | Тот же HTTP status, body и media type |
| Timeout, connection error или недоступность generic API | `503`, `{"status":"error","code":"dependency.unavailable"}` |

Adapter не подтверждает callback до получения ответа API. Он не повторяет вызов API самостоятельно: provider v0.2.0 повторяет callback по своему policy.

## Adapter -> `receipt.accept`

Adapter вызывает generic route:

```http
POST /api/receipt/accept
Authorization: Bearer <PROVIDER_CALLBACK_TOKEN>
Content-Type: application/json
Idempotency-Key: <messageId>
X-Action-Version: 1
X-Provider-Signature: v1=<lowercase-hex-hmac-sha256>
```

Receipt сериализуется compact JSON с sorted keys, без BOM и завершающего LF. SHA-256 body hash и HMAC вычисляются над одними и теми же exact HTTP body bytes.

Успешный subject envelope содержит:

```json
{
  "status": "ok",
  "outcome": "RECEIVED",
  "result": {
    "messageId": "provider-123",
    "externalRequestId": "external-123",
    "state": "RECEIVED"
  },
  "meta": {
    "correlationId": "00000000-0000-0000-0000-000000000000",
    "actionVersion": 1
  }
}
```

`outcome` равен `RECEIVED` для нового сообщения или `DUPLICATE` для уже известного идентичного сообщения, принятого с другой idempotency-командой. `state` отражает сохранённое состояние `RECEIVED` или `APPLIED`.

| Ситуация | HTTP status | `code` |
|---|---:|---|
| Подпись отсутствует у `receipt.accept` | `403` | `receipt.signature_required` |
| Формат/version/HMAC подписи неверны | `401` | `signature.invalid` |
| Receipt schema или `version` неверны | `422` | `payload.invalid` |
| `externalRequestId` неизвестен | `422` | `receipt.external_request_not_found` |
| Тот же idempotency key или `messageId`, но другой body | `409` | `idempotency.conflict` |

Идентичный повтор с теми же `Idempotency-Key` и body возвращает те же `status`, `outcome` и `result`. Поле `meta` и transport headers могут отличаться.

## Повтор `workflow.manual`

Идентичный повтор с теми же key и payload возвращает те же `status`, `outcome` и `result`; `meta` и transport headers могут отличаться. Изменённый payload с тем же key возвращает `409 idempotency.conflict`. Конкурирующее решение уже завершённого шага возвращает `409 workflow.decision_conflict`.

## Повтор `payment.submit`

Первый успешный submit возвращает subject result со `status = PROCESSING` и закреплёнными `processId`, `flowName`, `flowVersion`.

Любой последующий submit той же operation не создаёт новый process и возвращает исходный закреплённый subject result команды со `status = PROCESSING`, даже если operation уже завершена или default flow version была изменена. Актуальное состояние operation читается через опубликованную read boundary, а не подменяет результат submit. Изменённый body с тем же idempotency key возвращает `409 idempotency.conflict`.
