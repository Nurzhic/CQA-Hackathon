# Этап 3 — итоговый отчёт

Готов benchmark и dry-run preflight. **Новых платных запросов: 0.** Исторический счётчик MultiAI остаётся 5; ALLOW_PAID_CALLS=false. Реальные PROD отсутствуют, results/prod/results.json не создан. Исходные 65 DEV сверены с локальным исходным API-ответом и не изменены.

## Готовность

- multilingual-v1: 18 случаев, 6 RU / 6 KZ / 6 EN; 17 исходных синтетических DEV + один явно собственный RU/KZ кейс. ID/текст/Official v1 закреплены SHA-256. Calibration/holdout 9+9, по три каждого языка.
- Покрыты code-switching, N/A, критические нарушения, отсутствие идентификации, плохое завершение, спорная консультация, неполные данные и корректное обслуживание.
- Реальных человеческих разметок: 0. Статус **unlabeled**, accuracy не вычислена. Готовы пустые A/B/consensus-шаблоны, независимая форма, разногласия и явное версионное согласование. Ответы моделей не стали Ground Truth.
- Отчёт использует одинаковые пары входов и одну версию pipeline; final_score не подменяет accuracy. Метрики языка/критерия, N/A matrices, critical P/R, evidence/цитаты, mean/median/p95, retries/validation failures/usage, денежное покрытие и ключевые credit deltas реализованы.
- Цена, вызовы и параметры закрепляются в одноразовом плане. Есть глобальный и атомарный run-бюджет, ограниченные concurrency/total timeout/retry/Retry-After, deadline. Несоответствие digest/количества/бюджета блокирует расход.

## Будущий preflight (не выполнялся)

План `data/stage3-preflight-final.json`: RU p1-ru-002; KZ cqa-synthetic-kz-codeswitch-v1 (собственный); EN p1-en-014. По три на обеих моделях — **6 оценок**, ожидается **12 HTTP generations**, верхняя граница **24**. Это не шесть HTTP-запросов.

| Модель | Оценок | Консервативный резерв кредитов | По 20 ₽/млн |
|---|---:|---:|---:|
| gpt-6.1-sol | 3 | 789,324 | 15.78648 ₽ |
| gemini-3.8-flash | 3 | 364,876 | 7.29752 ₽ |
| Всего | 6 | 1,154,200 | 23.08400 ₽ |

Эквивалент по сохранённому подтверждённому курсу на 2026-11-07: **≈ $0.28104**. Предлагаемый cap **$0.50** требует отдельного согласия. Это резерв, не фактическая стоимость. Условия каталога перепроверяются read-only перед исполнением, изменение тарифа требует нового плана. Для исторических 5 запросов + верхних 24 необходим общий request limit ≥29. Concurrency=1, timeout=90 с (общий на HTTP), attempts≤2, backoff≤30 с, deadline=3300 с.

## Команды без расходов

```sh
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
.venv/bin/python -m backend.app.batch_cli report
# Новый dry-run файл, если нужно пересоздать план:
.venv/bin/python -m backend.app.batch_cli preflight --out data/new-preflight.json --limit-usd 0.50
```

Открыть http://127.0.0.1:8000 → Benchmark. Формат разметки и будущие (только после согласия) команды исполнения: docs/STAGE3_BENCHMARK.md. Скрипт PROD требует ровно 10 настоящих входных чатов, отдельную модель/план/согласие, автоматически валидирует JSON 1.0 и только затем атомарно пишет файл. Команда и export проверены исключительно на явно тестовых временных данных.

## Проверки

- **79 backend-тестов passed**, 2 frontend-теста passed, production build passed.
- Временная SQLite + HTTP mocks: A/B/consensus, устаревшие версии, атомарный импорт/бюджет, одинаковые пары, отсутствие меток в запросах, nullable/boolean/integer различия, matrices, p95, цитаты, 429/Retry-After, общий timeout, deadline, безопасный preflight и 10-record PROD-export в pytest tmp_path.
- Прежние тесты Decimal/ROUND_HALF_UP/перераспределения N/A/шкал/критического флага/JSON 1.0 сохранены; добавлена проверка реального официального 50.625→50.63.
- Browser QA: unlabeled, 18 случаев, явно собственный code-switching, независимый пустой B, dry-run с расходами; новые платные запросы и человеческие значения не создавались.
- Один upstream deprecation warning Starlette TestClient/httpx; тесты проходят.

## Изменённые файлы

Новые:
- benchmarks/multilingual-v1.json, benchmarks/team_synthetic.json, benchmarks/README.md — фиксированные входы и происхождение.
- backend/app/benchmark_suite.py — неизменность, хеши, split и строгий model-input.
- backend/app/annotations.py — независимые человеческие раунды, атомарный импорт, консенсус.
- backend/app/benchmark_metrics.py, backend/app/benchmark_report.py — парные метрики/telemetry.
- backend/app/batch.py, backend/app/batch_cli.py, backend/app/benchmark_routes.py — офлайн-планы, согласие, runner, API, PROD-export.
- frontend/src/Benchmark.tsx — независимая разметка, сравнение, dry-run.
- backend/tests/test_stage3.py — 19 новых сценариев.
- docs/STAGE3_PLAN.md, docs/STAGE3_BENCHMARK.md, output/STAGE3_REPORT.md, output/stage3-benchmark.png.

Минимально расширены:
- backend/app/storage.py — отдельные таблицы разметки/планов/run-бюджета.
- backend/app/engine.py — безопасный аудит validation failures, bounded backoff.
- backend/app/multiai.py — общий timeout, Retry-After, конфигурация экземпляра, run-ID.
- backend/app/gateway_budget.py — атомарный глобальный + run лимит.
- backend/app/main.py — подключение router; legacy одиночные draft-метки больше не эталон.
- backend/app/cli.py — запрет неподтверждённого single-file Ground Truth в legacy отчёте.
- backend/tests/test_multiai_api.py — ожидание изменено: одиночный draft не даёт accuracy.
- frontend/src/MultiAI.tsx, frontend/src/style.css — новый компонент benchmark и его layout.
- README.md, IMPLEMENTATION_PLAN.md, .env.example; в output/MULTIAI_SMOKE.md исправлено только округлённое значение конверсии миллион-кредитов (исторические результаты не изменены).

Domain/scoring/checklist/exporter, официальные шкалы и формат JSON 1.0 не переписаны. Рабочая .env с ключом не изменена.

## Не разрешено и ещё не известно

Нет человеческого Ground Truth, поэтому нельзя утверждать повышение accuracy или превосходство модели. Нужны эксперты A/B и арбитраж, особенно по консультации и применимости N/A; нужны проверенные корпоративные регламенты. Имена экспертов без авторизации — самодекларация; независимость организует команда. Цитаты доказывают наличие текста, но не семантическую достаточность вывода.

Новый preflight 6 оценок/$0.50 не согласован. Ранее выданное разрешение покрывало только прошедший RU smoke. Не проводились ни новый RU/KZ/EN платный тест, ни полный benchmark, ни обработка PROD. Общую дельту счётчика ключа при concurrency нельзя честно разнести по моделям без отдельного provider billing; неизвестная стоимость остаётся null.

Следующий шаг — человеческая разметка и отдельное согласие на конкретный preflight-план, число оценок/HTTP и бюджет. До него исполнение остановлено.
