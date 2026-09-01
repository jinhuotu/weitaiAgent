from datetime import datetime, timezone, timedelta

from common.times import to_epoch_ms


def test_naive_mysql_utc_wall_clock() -> None:
    dt = datetime(2026, 8, 19, 9, 38, 8)
    expected = int(datetime(2026, 8, 19, 9, 38, 8, tzinfo=timezone.utc).timestamp() * 1000)
    assert to_epoch_ms(dt) == expected


def test_strips_driver_local_tz_keeps_wall_clock() -> None:
    cst = timezone(timedelta(hours=8))
    tagged = datetime(2026, 8, 19, 9, 38, 8, tzinfo=cst)
    naive = datetime(2026, 8, 19, 9, 38, 8)
    assert to_epoch_ms(tagged) == to_epoch_ms(naive)


def test_none_is_zero() -> None:
    assert to_epoch_ms(None) == 0
