# Неделя 3. Python-периметр

Это продолжение заданий недель 1 и 2 в том же репозитории участника. C# gateway/API/worker и PostgreSQL workflow остаются ядром. Участник добавляет один Python 3.12+ integration image с тремя entrypoints: `outbox-dispatcher`, `receipt-adapter`, `inbox-reconciler`.

## Материалы

- [Условие задания](task/assignment.md)
- [Памятка участника](task/student-handout.md)
- [Контракт проверки](autocheck/README.md)
- [Machine-readable contracts](contracts/course-1)
- [Выданная карта payment-processing v1](contracts/course-1/payment-processing-v1.flow.yaml)

## Проверяемый результат

Python dispatcher доставляет durable Outbox в provider v0.2.0, Python adapter переводит legacy callback в signed receipt v1, Python reconciler применяет Inbox. PostgreSQL остаётся источником истины, а обе карты исполняются generic C# worker без branches по имени flow/action.

## Открытая проверка

Требуются Python 3.11+ для checker, Docker Engine и Docker Compose v2 с `!override`, `!reset` и `config --no-env-resolution`.

```bash
./check.sh
```

Checker:

- выполняет Compose admission и cold build;
- поднимает отдельный project с synthetic secrets;
- проверяет C#/Python/provider image boundaries;
- останавливает и запускает dispatcher;
- проводит provider success, duplicate/conflict callback и обе review branches;
- проверяет stable views и least-privilege roles;
- пересоздаёт Python services;
- записывает `week-3-public-report.json` без баллов и секретов;
- удаляет project, volumes и созданные локальные images, если не передан `--keep-stack`.

Коды завершения:

- `0` — все public checks пройдены;
- `1` — нарушен контракт решения;
- `2` — checker или локальное окружение не готовы.

Checker не запускает host scripts сдачи и не читает физические предметные таблицы. Authoritative seams: HTTP actions, provider audit и read-only schema `autocheck`.
