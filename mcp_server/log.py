"""Structured logging for psamvault-mcp.

All log output goes to stderr (stdout is reserved for the MCP JSON-RPC transport).
Configure level via the PSAMVAULT_LOG_LEVEL env var (default: INFO).
"""

import logging
import os
import sys


class _StderrHandler(logging.Handler):
    """Handler that writes to sys.stderr at emit time (not import time).

    This lets pytest's capsys fixture capture log output correctly.
    """

    def __init__(self) -> None:
        super().__init__()
        self.terminator = "\n"

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        sys.stderr.write(msg + self.terminator)


_LOG: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """Return the application-wide psamvault logger.

    Configured once on first call — subsequent calls return the same instance.
    """
    global _LOG
    if _LOG is not None:
        return _LOG

    level = os.getenv("PSAMVAULT_LOG_LEVEL", "INFO").upper()

    _LOG = logging.getLogger("psamvault")
    _LOG.setLevel(level)
    _LOG.propagate = False  # Don't double-emit via root logger

    handler = _StderrHandler()
    handler.setFormatter(logging.Formatter(
        "psamvault: %(levelname)s %(message)s"
    ))
    _LOG.addHandler(handler)

    return _LOG