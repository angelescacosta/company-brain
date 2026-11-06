"""T39.AC2 PBT: retention keeps exactly the archives of the last 30 days, or only the newest
one (with a warning) when none is that recent. Protects against losing every backup when
backups have been failing, and against keeping client data longer than disclosed."""

from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from company_brain.backup import KEEP_DAYS, retention

NOW = datetime(2026, 11, 6, 2, 0)
ages = st.lists(st.integers(0, 120 * 24), max_size=60, unique=True)  # hours old


@given(ages)
def test_retention(hours: list[int]) -> None:
    dates = [NOW - timedelta(hours=h) for h in hours]
    keep, warning = retention(dates, NOW)
    recent = {d for d in dates if NOW - d <= timedelta(days=KEEP_DAYS)}
    if recent:
        assert set(keep) == recent and not warning
    elif dates:
        assert keep == [max(dates)] and warning
    else:
        assert keep == [] and not warning
