# Неделя 3. Памятка участника

## Три Python-процесса

| Процесс | Вход | Выход | Не делает |
|---|---|---|---|
| `outbox-dispatcher` | Fixed SQL claim | Provider HTTP | Не меняет operation/process |
| `receipt-adapter` | Legacy callback | Signed receipt v1 | Не подключается к PostgreSQL |
| `inbox-reconciler` | Fixed SQL function | Persisted workflow signal | Не исполняет flow в Python |

Все три запускаются из одного локально собранного image, но получают разные credentials.

## Stable identifiers

```text
requestId -> operationId -> processId -> jobId -> executionId
          -> externalRequestId -> providerPaymentId/messageId
```

Provider body `operationId` содержит ваш `externalRequestId`. Это legacy wire name, а не причина объединять предметные идентификаторы.

## Receipt mapping

```text
providerPaymentId -> messageId + providerPaymentId
operationId       -> externalRequestId
result            -> outcome
occurredAt        -> same raw string
message           -> validate and discard
```

Compact sorted JSON подписывается как exact bytes. Не подписывайте повторно сериализованный object на принимающей стороне: generic API проверяет исходный body.

## Crash questions

1. Что сохранено, если dispatcher остановлен до HTTP?
2. Что произойдёт, если provider принял payment, но response потерян?
3. Может ли callback подтвердить Outbox раньше `succeed_outbox`?
4. Кто применит Inbox после recreate reconciler?
5. Почему adapter не должен иметь database credentials?

## Минимальные Python tests

- strict legacy callback validation;
- mapping всех полей и preservation timestamp string;
- compact sorted JSON bytes;
- HMAC raw secret semantics;
- HTTP retry classification;
- stable provider key/body across retries;
- adapter response mapping 2xx/4xx/5xx;
- configuration rejects missing secrets/URLs.

## Типовые ошибки

- flow binding или limit перенесены в Python;
- dispatcher читает/обновляет physical Outbox table;
- новый external key на retry;
- adapter хранит callback в памяти и отвечает до API commit;
- HMAC считается над произвольным JSON object, а не exact bytes;
- `messageId` генерируется заново для duplicate callback;
- receipt сразу ставит operation final status, обходя workflow;
- one Python service получает все credentials;
- provider v0.2.0 ошибочно считается Python или native receipt-v1 sender.
