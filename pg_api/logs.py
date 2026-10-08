"""JSON logs with correlation IDs (CLAUDE.md engineering standards).

`request_id`, `job_id`, `build_id`, `run_id`, `candidate_id` are bound with structlog's context
variables, so every line logged while handling a request or a job carries them. Secrets are never
passed to a logger.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get(name: str) -> Any:
    return structlog.get_logger(name)
