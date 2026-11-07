# MultiAI integration — stage 2

## Verified without generation

Checked on 2026-11-07 using the existing backend-only `.env` key; the file was not overwritten.

- Base URL: `https://multiai.store/v1`.
- Authenticated `GET /v1/models`: HTTP 200, 64 catalog entries.
- Catalog IDs `gpt-6.1-sol` and `gemini-3.8-flash`: both `availability=available`.
- Authenticated `GET /v1/subscription/limits`: HTTP 200. Current billing mode is `tokens`.
- `POST /v1/chat/completions`: documented in the official client, implemented and tested with mocked HTTP. **At the initial discovery stage no real POST was made.** Subsequently the approved smoke below verified generation on both exact model IDs.

Official sources:
- [MultiAI website](https://multiai.store/).
- [Official client documentation](https://multiai.store/app.js): `DOCS_CONTENT` sections for authentication, models and tokens; contains the Bearer request example, Chat Completions URL and billing formula.
- [Terms](https://www.multiai.store/terms.html): credits are internal accounting units, availability/routes/tariffs can change.

The catalog reports Gemini as `agentic_compatibility=degraded`, Sol as `full`. This is metadata, not a quality benchmark or proof of JSON Schema support. A successful read-only catalog request does not prove that a generation will be admitted by the account tariff, quota and active route.

## Configuration

```dotenv
MULTIAI_API_KEY= # existing secret, backend only
MULTIAI_BASE_URL=https://multiai.store/v1
MULTIAI_PRIMARY_MODEL=gpt-6.1-sol
MULTIAI_SECONDARY_MODEL=gemini-3.8-flash
MULTIAI_RESPONSE_FORMAT=json_schema
MULTIAI_TIMEOUT_SECONDS=90
MULTIAI_MAX_OUTPUT_TOKENS=6000
ALLOW_PAID_CALLS=false
API_BUDGET_USD=10
MULTIAI_MAX_REQUESTS=12
MULTIAI_MAX_BATCH=1
MULTIAI_ALLOW_BULK=false
MULTIAI_ALLOW_REAL_CALLS=false
MULTIAI_USD_PER_MILLION_CREDITS=
MULTIAI_PRICING_CONFIRMED=false
```

Existing `MULTIAI_MODEL_1` and `MULTIAI_MODEL_2` are read as aliases if the canonical variable is absent. The configured models must match live catalog entries. No model substitutions or third-party destinations are selected automatically. Base URL validation prevents accidentally sending the key to another host. Official OpenAI/Gemini adapters from stage 1 remain only for regression compatibility; the production provider factory and UI use Mock or MultiAI.

## Pipeline and validation

1. Validate/canonicalize the transcript without rewriting text, language, timestamp or order.
2. Fact extraction call: request, greeting, identification/protected action order, request completeness, empathy, closure, critical events and language; criterion applicability and literal quotations.
3. Criteria evaluation call: original transcript + verified facts + saved checklist + exact output schema. Models cannot choose weights or calculate totals.
4. Validate Pydantic types, all criterion IDs, scales, N/A reasons/applicability, uncertainty and evidence boundaries.
5. Require an exact nonempty original quotation for every evidence turn. Fact and rating applicability must match. Evidence pointing to an existing turn but quoting invented text is rejected.
6. Use unchanged Decimal score engine, persist result, model ID, per-call stage/latency/usage/billing and audit history.

Quotes verify textual grounding, not semantic truth. Human review and independent ground truth remain necessary, especially for consultation correctness and subtle N/A cases.

At most two evaluation attempts. Verified facts are reused when retrying only the assessment; worst case is four generation calls per dialogue. A 400/422 explicitly rejecting `response_format`/`json_schema` switches to schema-in-instructions mode on the next bounded attempt. Other request errors do not silently downgrade the protocol. Auth, access, missing model, quota, unapproved paid calls and model substitution fail without repeated generation. Timeout, 429, 5xx and malformed model output have bounded retry. The actual response model must exactly match the chosen ID; otherwise reject the result and preserve the observed ID in request telemetry.

## Spending

Gateway credits, upstream usage and USD are separate fields. Raw numeric usage is preserved. USD is reported as actual cost only when the response explicitly labels a billing amount as USD; arbitrary `usage.cost` or credits never become dollars. Missing cost is `null`, with coverage shown.

Before every POST, reserve a conservative credit estimate using live catalog input/output multipliers, UTF-8 input bound + 4096 overhead and maximum output tokens. USD budget admission additionally requires a manually verified conversion for the actual account tariff via `MULTIAI_USD_PER_MILLION_CREDITS`, `MULTIAI_PRICING_CONFIRMED=true` and `ALLOW_PAID_CALLS=true`. A durable atomic ledger caps request count and USD reserves. The USD cap cannot exceed $10. Failed/timeout requests retain reserves because billing may have occurred. This conservative ledger can stop earlier than actual spending.

The account may contain old/new balances with different economics. The public new-credit price is not automatically applied to existing funds. Multipliers can change; model authenticity, provider token accounting, exchange rates and tariff conversion cannot be independently guaranteed by this client. Set a provider-side key spend limit for a hard wallet boundary. The local budget is based on verified conversion and conservative estimates, not a gateway invoice.

Bulk processing needs both an increased `MULTIAI_MAX_BATCH` and, above 3 dialogues, `MULTIAI_ALLOW_BULK=true`. Initial tests exclude voice calls. Real calls require separate permission plus both `MULTIAI_ALLOW_REAL_CALLS=true` and `allow_real_calls=true`; ordinary UI never enables this.

## Safe first test, after explicit approval

1. Confirm the actual USD price per million MultiAI credits (including the applicable old/new tariff).
2. Set a small `API_BUDGET_USD`, verified conversion, `MULTIAI_PRICING_CONFIRMED=true`, and only then `ALLOW_PAID_CALLS=true`. Preserve the existing key. Restart backend.
3. Select **one synthetic chat** and a configured model in Conversations.
4. Click Evaluate. The backend first performs only a read-only catalog request and returns a quote. Review credits, USD reservation and expected/worst request count.
5. Confirm the paid launch in the modal. Real errors are shown and persisted; a failed model response never becomes a zero score.
6. Inspect facts, exact evidence, criteria, latency, usage and request statistics before increasing the test scope. Keep bulk and real-call flags false.

Useful local endpoints:

```text
GET  /api/multiai/connection       catalog + account/key limits, no generation
GET  /api/multiai/models           live model catalog
GET  /api/multiai/stats            local ledger, usage, reported costs, requests
POST /api/evaluate/estimate        selected chat quote, no generation
POST /api/evaluate                 paid only behind server and per-launch gates
GET  /api/benchmark/sample         deterministic 6 RU + 6 KZ + 6 EN chats
POST /api/benchmark/labels         independent expert labels, immutable versions
GET  /api/benchmark/labels         expert label history
GET  /api/benchmark/report         per-model comparison, no generation
```

## Non-paid preflight

A local preflight was calculated against the retrieved catalog for the first selected RU/KZ/EN synthetic chats, with the current 6000 output-token ceiling. It includes up to two attempts at both stages and a generous stage-2 fact payload allowance:

| Model | 3-chat conservative credit reservation | Expected / maximum generation requests |
|---|---:|---:|
| gpt-6.1-sol | 780,528 | 6 / 12 |
| gemini-3.8-flash | 361,888 | 6 / 12 |
| Both | 1,142,416 | 12 / 24 |

For one first RU chat: Sol ≤260,820 credits; Gemini ≤120,848 credits, including retry allowance. These are estimates of reserved internal units, **not quoted USD costs** or promised final charges. USD is unknown until the account conversion is confirmed. Current defaults (one dialogue per launch, 12 total requests) intentionally do not authorize a full 65-dialogue run.

Private read-only catalog/limit snapshots and preflight live under ignored `data/`. The initial preflight made no generation calls; the subsequent approved smoke is described below.

## Benchmark and expert labels

Benchmark selection excludes every `voice call`, takes 6 sorted IDs in each RU/KZ/EN group, and uses the same 18 IDs for both exact configured model IDs. The UI's Benchmark page supports manual JSON expert labels and immutable label history. For N/A, labels require `na_reason`. It does not prefill ground truth from a model prediction. Latest independent expert version is used; model predictions come only from unreviewed Official v1 real evaluations, preventing appeal decisions from leaking the labels into model accuracy.

Report: accuracy per criterion/language, binary applicability (N/A) accuracy, scale MAE, critical precision/recall, evidence ID validity, exact-reference evidence precision, latency, numeric usage, known USD cost and cost coverage. Missing ground truth gives no accuracy claim; matched conversation count is explicit. The report does not rank models without actual labeled predictions.

## Tests

All automated calls use HTTP mocks and isolated SQLite databases, never the real balance. Coverage includes both models and RU/KZ/EN, valid/invalid JSON, wrong scale/IDs, missing/duplicate criteria, fake/out-of-bounds quotes, N/A contradictions, empty output, timeout, HTTP 401/403/402/404/429/500, unsupported JSON Schema fallback, model availability/substitution, budget/request limits, data guards and API→persistence→expert-label→report workflow.

## Approved live smoke completed

On 2026-11-07 the user confirmed the 20 RUB/million-credit tariff and authorized one RU chat on both models within $0.50. CBR USD/RUB was verified as 82.1386; conversion was calculated with Decimal. Secret env entries were preserved.

Both `gpt-6.1-sol` and `gemini-3.8-flash` returned valid evaluations of `p1-ru-001` with exact matching model IDs, valid quotes and all eight values in agreement: final 92.31, no critical flag. Sol needed two validation attempts (3 generation calls); Gemini succeeded on its first attempt (2 calls). JSON Schema requests were accepted; neither model required prompt-mode fallback. No accuracy comparison is claimed without expert ground truth.

Observed API-key billing counter delta: 31,589 credits = 0.63178 RUB ≈ $0.007692, including the validation retry. The gateway did not provide an explicit USD invoice field. Local conservative reservations remain $0.033676 and are separate from this observed charge.

The pricing conversion is now recorded in `.env`; persistent `ALLOW_PAID_CALLS=false` remains unchanged. No authorization for other dialogues, KZ/EN, voice calls or the full DEV batch is inferred. See `output/MULTIAI_SMOKE.md` for the sanitized test summary.
