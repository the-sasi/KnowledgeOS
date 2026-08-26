"""Console logging setup.

Deliberately plain: a readable stderr format for local development. Structured
logging and tracing belong to `services/observability/` when that exists.
"""

from __future__ import annotations

import logging
import sys

from knowledgeos.config import settings

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s  %(levelname)-7s %(name)-28s %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, (level or settings.log_level), logging.INFO))

    # urllib3 logs every connection at DEBUG; too noisy for our purposes.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
