"""Domain-level exceptions."""

from __future__ import annotations


class JobNotFoundError(LookupError):
    """Raised when a job id cannot be resolved."""


class JobValidationError(ValueError):
    """Raised when job input is invalid."""
