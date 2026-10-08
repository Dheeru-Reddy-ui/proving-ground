# Holdout freeze

The holdout bugs of `benchmark/bugs.yaml` are frozen. Every benchmark run recomputes the
digest below (`pg bugs check`) and refuses to run if it differs. Changing the holdout
split after this point needs Dheeru's approval and a new freeze (CLAUDE.md rule 9).

- holdout_sha256: `0d4edd7b05ba29589326ead89f73024cb5c715d7b4438c49f54ceccd2f70f5aa`
- bugs_yaml_commit: `cf8c8894b7e58784f27c69ccdcbf086a172dd7b6`
- frozen_on: `2026-10-08`
- holdout_ids: SB01, SB03, SB06, SB07, SB10, SB12

The digest is sha256 over the holdout entries as canonical JSON (sorted by id, keys sorted), computed by `pg_core.catalog.holdout_digest`.
