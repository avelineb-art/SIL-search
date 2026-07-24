"""Structured-ish logging setup shared by the CLI.

Every log record includes the stage/module name and level in a consistent,
greppable format; failures that need to be queryable later (search
failures, quota errors, blocked pages, parsing errors, etc.) are also
written to the `errors` table by the calling code - this configures the
console/file logging layer, not the DB error log.
"""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        # Already configured (e.g. re-entrant CLI invocation in tests).
        root.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    )
    root.addHandler(handler)
    root.setLevel(level)
