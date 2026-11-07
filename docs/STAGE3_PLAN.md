# Stage 3 audit and minimal plan

Baseline: Conversation.language is RU/KZ/EN; transcript roles and timestamps are preserved. EvaluationRow holds immutable model responses/usage/latency, checklist_id and appeal revisions. ExpertRow holds independent-looking but unpaired single-reviewer drafts. The legacy report uses the latest such draft and sorts the first six IDs per language, lacks frozen input hashes, consensus, fair matched model coverage and complete telemetry.

Reuse domain/checklist/score/exporter and two-stage MultiAI. Add pinned 18-case suite (17 existing DEV + one clearly team-authored RU/KZ code-switch case), separate immutable annotation rounds A/B/consensus, paired reports, offline estimates and guarded batch CLI. Existing legacy draft endpoints remain import/history tools, never become consensus automatically.

Plan:
1. Pin inputs/checklist hashes, coverage and preflight IDs; never modify DEV.
2. Add blind independent A/B, disagreements, explicitly pinned consensus and invalidation on label revision.
3. Extend offline criterion metrics and unlabelled telemetry; exclude score and reviewed predictions from accuracy.
4. Add validation-error audit, bounded Retry-After/backoff, concurrency/timeouts and offline preflight/prod plans.
5. Add mock-only tests, UI annotation/preflight/report flows and documentation. Stop before paid execution.
