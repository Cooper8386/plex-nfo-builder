"""Shared handling for provider rate-limit headers."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_delay(value: str | None) -> float:
    """Accept Retry-After seconds or HTTP dates, with a bounded wait."""
    try:
        delay = float(value or "5")
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value or "") - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 5.0
    return max(0.0, min(60.0, delay))
