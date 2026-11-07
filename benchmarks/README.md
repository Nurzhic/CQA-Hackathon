# Frozen benchmark assets

`multilingual-v1.json` pins 18 cases (6 RU / 6 KZ / 6 EN), hashes and coverage tags. Organizer transcripts are referenced by ID and SHA-256 only and remain in ignored local data/SQLite.
`team_synthetic.json` contains only explicitly team-authored synthetic content; it is never DEV or PROD. Coverage tags describe test scenarios, not ground-truth criterion values, and are never sent to models.

All ground truth starts unlabeled. Two independent reviewers and explicit consensus are required. Never copy AI outputs into expert labels.
