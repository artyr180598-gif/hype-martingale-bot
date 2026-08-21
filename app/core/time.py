from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return an aware UTC timestamp for all backend event timestamps."""
    return datetime.now(timezone.utc)
