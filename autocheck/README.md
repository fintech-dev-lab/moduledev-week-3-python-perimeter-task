# Контракт public checker недели 3

## Доверенные seams

- Compose services из задания;
- gateway на host-порту 8080 в исходном contract и на изолированном случайном loopback-порту при проверке;
- generic HTTP actions;
- provider v0.2.0 audit;
- PostgreSQL database `course`, `psql` и read-only schema `autocheck`;
- fixed integration functions/roles;
- public fixtures с закреплённым digest.

Checker не вызывает `course.sh` на host и не использует physical tables/ORM/class names.

Запуск из отдельного клона checker:

```bash
./check.sh --repo /path/to/participant-solution
```

`cli` обязан быть объявлен, но может успешно завершаться как one-shot service. Checker не требует имени `dotnet` у C# process: technology boundary подтверждается контрактом предыдущих недель, поведением services и неизменностью C# images в hidden run.

## Public phases

| Phase | Проверка |
|---|---|
| `admission` | Safe Compose без host-backed resources, required services, Python main runtimes и locally built Python image |
| `startup` | Cold build, readiness, типизированные views, contract info, functions и roles |
| `outbox` | Dispatcher stop, durable Outbox, resume, one provider payment |
| `receipt` | Missing/invalid signature, exact legacy mapping, duplicate/conflict |
| `review` | Limit-rule и manual branches |
| `recovery` | Recreate Python services, persisted state |
| `security` | Заявленные DB principals, отсутствие role memberships/лишних grants, secret/full-message redaction |

## Hidden extensions

Hidden checker недели 3 использует trusted capture sidecars для exact provider request и exact adapter request, early callback, все provider modes, malformed JSON, HTTP classification и concurrent submit/manual calls.

Детерминированные failpoints относятся к будущей приёмке недели 4 и не входят в обязательный объём или оценку недели 3.

## Report

`week-3-public-report.json` содержит только status, named checks, safe expected/actual summaries и выполненные commands. Public artifact не содержит score, criterion IDs, payloads, secrets и exact hidden inputs.
