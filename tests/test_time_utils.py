from datetime import datetime

from backend.time_utils import local_day_start_end_to_utc, parse_storage_ts, to_utc_z


def test_parse_storage_ts_local_naive_to_utc():
    parsed = parse_storage_ts("2026-03-20T15:03:08.990401", naive_strategy="local")

    assert parsed is not None
    assert parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") == "2026-03-20T07:03:08.990401Z"


def test_parse_storage_ts_utc_naive_to_utc():
    parsed = parse_storage_ts("2026-03-20T15:03:08.990401", naive_strategy="utc")

    assert parsed is not None
    assert parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") == "2026-03-20T15:03:08.990401Z"


def test_to_utc_z_keeps_explicit_offset_instant():
    assert to_utc_z("2026-03-20T23:03:08.990401+08:00", naive_strategy="utc") == "2026-03-20T15:03:08.990401Z"


def test_local_day_start_end_to_utc_uses_shanghai_midnight():
    start_iso, end_iso = local_day_start_end_to_utc("2026-03-21")

    assert start_iso == "2026-03-20T16:00:00Z"
    assert end_iso == "2026-03-21T16:00:00Z"
