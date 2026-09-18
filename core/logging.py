"""Logging helpers for data-quality dimensions.

The step numbers used by callers must follow ``docs/DATA_QUALITY_ANALYSIS_GUIDE.md``.
This module only adds logging context; it does not log connection details or query data.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO


DIMENSION_LOG_FORMAT = "[%(dimension)s] %(message)s"
STEP_LOG_FORMAT = "\t[Step %(step)s] %(message)s"
LOGGER_NAME = "data_quality"


class HierarchicalFormatter(logging.Formatter):
    """Format dimension messages and indented step messages separately."""

    def format(self, record: logging.LogRecord) -> str:
        if hasattr(record, "step"):
            message = STEP_LOG_FORMAT % {
                "step": record.step,
                "message": record.getMessage(),
            }
        else:
            message = DIMENSION_LOG_FORMAT % {
                "dimension": record.dimension,
                "message": record.getMessage(),
            }
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        return message


class DimensionLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter for messages belonging to a dimension."""

    def for_step(self, step: int | str) -> "StepLoggerAdapter":
        """Return a separate logger for one documented step."""
        return StepLoggerAdapter(self.logger, {"dimension": self.extra["dimension"], "step": step})


class StepLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter for an indented step within a dimension."""


def _configure_logger(
    *,
    level: int,
    stream: TextIO | None,
    name: str,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if not any(getattr(handler, "_data_quality_handler", False) for handler in logger.handlers):
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(HierarchicalFormatter())
        handler._data_quality_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


def setup_dimension_logger(
    dimension: str,
    *,
    level: int = logging.INFO,
    stream: TextIO | None = None,
    name: str = LOGGER_NAME,
) -> DimensionLoggerAdapter:
    """Return a logger for messages at dimension level."""
    logger = _configure_logger(level=level, stream=stream, name=name)
    return DimensionLoggerAdapter(logger, {"dimension": dimension})


def setup_step_logger(
    dimension: str,
    step: int | str,
    *,
    level: int = logging.INFO,
    stream: TextIO | None = None,
    name: str = LOGGER_NAME,
) -> StepLoggerAdapter:
    """Return an indented logger for one documented step."""
    logger = _configure_logger(level=level, stream=stream, name=name)
    return StepLoggerAdapter(logger, {"dimension": dimension, "step": step})


def setup_logger(
    dimension: str,
    *,
    level: int = logging.INFO,
    stream: TextIO | None = None,
    name: str = LOGGER_NAME,
) -> DimensionLoggerAdapter:
    """Backward-compatible alias for :func:`setup_dimension_logger`.

    Example::

        setup_logger("Completeness").info("Starting dimension")
        setup_step_logger("Completeness", 1).info("Loading metadata")
    """
    return setup_dimension_logger(dimension, level=level, stream=stream, name=name)


__all__ = [
    "DIMENSION_LOG_FORMAT",
    "DimensionLoggerAdapter",
    "STEP_LOG_FORMAT",
    "StepLoggerAdapter",
    "setup_dimension_logger",
    "setup_logger",
    "setup_step_logger",
]
