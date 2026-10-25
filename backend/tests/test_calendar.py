from datetime import date

import pytest

from apps.core.calendar import WorkCalendar


def test_sunday_alert_is_on_saturday():
    assert WorkCalendar().previous(date(2026, 10, 4)) == date(2026, 10, 3)


def test_fresh_order_on_thursday_with_one_day_lead_is_saturday():
    assert WorkCalendar().add_workdays(date(2026, 10, 1), 1) == date(2026, 10, 3)


def test_friday_is_closed_and_saturday_is_open():
    assert not WorkCalendar().is_workday(date(2026, 10, 2))
    assert WorkCalendar().is_workday(date(2026, 10, 3))


def test_holiday_and_workday_override():
    calendar = WorkCalendar(
        holidays=frozenset({date(2026, 10, 4)}),
        extra_workdays=frozenset({date(2026, 10, 3)}),
    )
    assert calendar.add_workdays(date(2026, 10, 1), 1) == date(2026, 10, 3)
    assert calendar.on_or_after(date(2026, 10, 4)) == date(2026, 10, 5)


@pytest.mark.parametrize("lead", [-1, 1.5, True, 3651])
def test_invalid_lead_time_is_rejected(lead):
    with pytest.raises(ValueError):
        WorkCalendar().add_workdays(date(2026, 10, 1), lead)


def test_empty_calendar_is_rejected():
    with pytest.raises(ValueError):
        WorkCalendar(workdays=frozenset())
