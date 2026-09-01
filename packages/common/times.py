"""MySQL DATETIME has no timezone.

Docker MySQL defaults to UTC, so `NOW()` stores UTC wall-clock. Drivers often
return that as naive datetime, or attach the host local tz to the same numbers.
Always interpret the wall-clock as UTC before converting to epoch milliseconds.
"""

from __future__ import annotations

from datetime import datetime, timezone


def to_epoch_ms(dt: datetime | None) -> int:
    if dt is None:
        return 0
    utc = datetime(
        dt.year,
        dt.month,
        dt.day,
        dt.hour,
        dt.minute,
        dt.second,
        dt.microsecond,
        tzinfo=timezone.utc,
    )
    return int(utc.timestamp() * 1000)
