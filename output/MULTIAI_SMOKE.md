# MultiAI — approved smoke test

Date: 2026-11-07. Scope: synthetic DEV RU chat `p1-ru-001`, Official v1, two configured models. No voice calls, no other chats, no bulk processing.

| Model | Score | Critical | Generation requests | Validation attempts | Latency |
|---|---:|---|---:|---:|---:|
| gpt-6.1-sol | 92.31 | False | 3 | 2 | 126.64 s |
| gemini-3.8-flash | 92.31 | False | 2 | 1 | 56.29 s |

Both responses passed strict criterion, scale, N/A, exact quotation/evidence, model ID and Decimal score validation. All eight criterion values agree. This single unlabeled dialogue establishes connectivity and pipeline functionality, not benchmark accuracy.

| Criterion | Both models |
|---|---|
| contact | true |
| discovery | null |
| identification | null |
| consultation | 20 |
| empathy | true |
| dialogue | 10 |
| closure | false |
| critical | false |

The closure criterion failed: a farewell was present, but no summary or offer of further help. Discovery and identification were N/A.

## Spending

- User-confirmed tariff: 20 RUB per million MultiAI credits.
- CBR rate on 2026-11-07: 82.1386 RUB/USD; 1 million credits ≈ 0.24349088 USD.
- Combined preflight ceiling including retries: 381,668 credits, 7.63336 RUB ≈ 0.09293 USD.
- Observed API-key counter increase during this test: 31,589 credits = 0.63178 RUB ≈ 0.007692 USD.
- Conservative local USD reservation retained: 0.033676 USD; this is not the observed charge.
- Provider responses included numeric token usage but no explicit USD billing amount. The reported money amount above is a conversion of the observed key counter delta, not an API invoice.
- Approved overall cap: 0.50 USD. It was not reached.
- Paid access was enabled only inside the smoke process. Persistent ALLOW_PAID_CALLS remains false.

Full private telemetry is in ignored `data/multiai-approved-smoke.json`; evaluations #4 and #5 are saved in SQLite and available in the conversation card.

Exchange-rate source: https://www.cbr.ru/currency_base/daily/?UniDbQuery.Posted=True&UniDbQuery.To=07.11.2026

Backend regression: 60 tests passed.
