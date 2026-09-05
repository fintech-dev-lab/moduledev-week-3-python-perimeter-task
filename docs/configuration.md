# Конфигурация и запуск недели 3

## Подключение checker

Репозиторий задания и репозиторий решения остаются раздельными. Не копируйте `check.sh` поверх скриптов предыдущих недель.

```bash
git clone https://github.com/fintech-dev-lab/moduledev-week-3-python-perimeter-task.git
./moduledev-week-3-python-perimeter-task/check.sh --repo /path/to/participant-solution
```

Путь может быть абсолютным или относительным. Checker ищет Compose file в корне указанного решения и пишет туда `week-3-public-report.json`.

## Compose seam

Обязательные service names:

```text
gateway api cli postgres worker-a worker-b
outbox-dispatcher receipt-adapter inbox-reconciler provider-simulator
```

`cli` может быть постоянно работающим служебным сервисом или one-shot сервисом, который успешно завершает начальную настройку. Остальные services должны работать после `docker compose up -d --build`.

Только `gateway` публикует host port. Исходный Compose contract:

```yaml
services:
  gateway:
    ports:
      - "127.0.0.1:${COURSE_GATEWAY_PORT:-8080}:8080"
```

Checker сначала валидирует этот contract с `COURSE_GATEWAY_PORT=8080`, затем создаёт isolated override со случайным loopback host port и container target `8080`.

Python services и provider не публикуют host ports. Adapter не получает PostgreSQL settings. Dispatcher и reconciler используют отдельные roles.

## Переменные checker

Checker задаёт все значения из таблицы в isolated environment. Tracked Compose должен ссылаться на переменные, не содержать реальные secrets и не требовать `.env`. Для PostgreSQL password variables разрешён явно обозначенный local-only dev placeholder; checker всё равно заменяет его synthetic value.

| Переменная | Получатель | Обязательна | Default в tracked Compose |
|---|---|---:|---|
| `COURSE_GATEWAY_PORT` | `gateway` | Да | Разрешён `8080` |
| `COURSE_TEST_PROFILE` | Все проверяемые процессы | Да | Разрешён `1` |
| `COURSE_JWT_ISSUER` | C# application services | Да | Нет |
| `COURSE_JWT_AUDIENCE` | C# application services | Да | Нет |
| `COURSE_JWT_SIGNING_KEY` | C# application services | Да | Нет |
| `COURSE_POSTGRES_PASSWORD` | `postgres` | Да | Разрешён local-only placeholder |
| `COURSE_MIGRATOR_PASSWORD` | `postgres`, `cli` | Да | Разрешён local-only placeholder |
| `COURSE_PUBLISHER_PASSWORD` | `postgres`, `cli` | Да | Разрешён local-only placeholder |
| `COURSE_RUNTIME_PASSWORD` | `postgres`, `api` | Да | Разрешён local-only placeholder |
| `COURSE_WORKER_PASSWORD` | `postgres`, workers | Да | Разрешён local-only placeholder |
| `COURSE_OUTBOX_PASSWORD` | `postgres`, dispatcher | Да | Разрешён local-only placeholder |
| `COURSE_INBOX_PASSWORD` | `postgres`, reconciler | Да | Разрешён local-only placeholder |
| `PROVIDER_URL` | Dispatcher | Да | Разрешён service URL |
| `OUTBOX_OWNER` | Dispatcher | Да | Разрешён `outbox-dispatcher` |
| `PROVIDER_CALLBACK_CAPABILITY` | Adapter, provider callback URL | Да | Нет |
| `PROVIDER_CALLBACK_TOKEN` | Adapter | Да | Нет |
| `PROVIDER_HMAC_SECRET` | Adapter, API | Да | Нет |
| `RECEIPT_API_URL` | Adapter | Да | Разрешён gateway service URL |
| `PROVIDER_AUDIT_TOKEN` | Provider | Только checker | Нет |

Названия внутренних service-level переменных подключения к PostgreSQL можно выбирать самостоятельно. Compose должен получать их из перечисленных `COURSE_*_PASSWORD`, а runtime principal обязан совпадать с проверяемой role.

## Начальная настройка

После чистого `docker compose up -d --build` без ручных host-команд должны быть готовы:

- база `course`, roles и grants;
- migrations недель 1-3;
- зарегистрированные actions и ровно одна default version каждого действия;
- опубликованные и активированные flow maps;
- read-only schema `autocheck`;
- health/readiness gateway и фоновые workers.

Инициализация может выполняться PostgreSQL init scripts, one-shot `cli` или отдельным внутренним startup mechanism. Checker не фиксирует этот выбор и не запускает host scripts решения.

## SQL boundary Python

Имена, типы аргументов и tabular result `delivery.claim_outbox` закреплены в [полном контракте](04-week-3.md). `delivery.succeed_outbox` и `delivery.fail_outbox` возвращают JSON object; его внутренняя форма не стандартизована и не читается checker. Обязательны conditional owner/lease update и отсутствие regression из `CONFIRMED`.

`delivery.reconcile_inbox(p_limit integer)` возвращает число сообщений, применённых текущим вызовом. Физические таблицы, дополнительные внутренние функции и transaction implementation остаются выбором участника.
