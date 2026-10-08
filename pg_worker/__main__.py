"""`python -m pg_worker`: the standalone worker (the cloud image's worker entry point)."""

from pg_worker.runtime import run_standalone

run_standalone()
