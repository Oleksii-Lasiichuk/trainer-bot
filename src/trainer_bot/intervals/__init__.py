"""intervals.icu HTTP client + pydantic models."""

from .client import IntervalsClient
from .errors import IntervalsAPIError, IntervalsAuthError, IntervalsNotFoundError

__all__ = [
    "IntervalsClient",
    "IntervalsAPIError",
    "IntervalsAuthError",
    "IntervalsNotFoundError",
]
