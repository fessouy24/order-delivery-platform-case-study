"""Pure calendar rules. Persistent business configuration follows in production scheduling."""

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class WorkCalendar:
    # Python weekday: Monday=0, Sunday=6.
    workdays: frozenset[int] = frozenset({5, 6, 0, 1, 2, 3})
    holidays: frozenset[date] = frozenset()
    extra_workdays: frozenset[date] = frozenset()

    def __post_init__(self):
        if not self.workdays or not self.workdays <= frozenset(range(7)):
            raise ValueError("Configure at least one valid workday")
        if self.holidays & self.extra_workdays:
            raise ValueError("A date cannot be both a holiday and an extra workday")

    def is_workday(self, day: date) -> bool:
        return day in self.extra_workdays or (
            day.weekday() in self.workdays and day not in self.holidays
        )

    def _seek(self, day: date, direction: int) -> date:
        for _ in range(3660):
            if self.is_workday(day):
                return day
            day += timedelta(days=direction)
        raise ValueError("No valid workday within the scheduling horizon")

    def on_or_after(self, day: date) -> date:
        return self._seek(day, 1)

    def previous(self, day: date) -> date:
        return self._seek(day - timedelta(days=1), -1)

    def add_workdays(self, day: date, count: int) -> date:
        if type(count) is not int or count < 0 or count > 3650:
            raise ValueError("Lead time must be an integer between 0 and 3650 workdays")
        if count == 0:
            return self.on_or_after(day)
        for _ in range(count):
            day = self._seek(day + timedelta(days=1), 1)
        return day
