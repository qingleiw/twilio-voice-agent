"""The business behind the phone line — here, a made-up one that keeps its diary in memory.

This is the file you replace. Everything above it talks to these four functions and nothing else,
so pointing the agent at a real booking system, a practice management database or a CRM means
rewriting this module and leaving the rest alone.
"""
import os
from datetime import date, time, timedelta

NAME = os.getenv("BUSINESS_NAME", "Northgate Bicycle Workshop")
SLOT_MINUTES = 30

HOURS = {
    "monday": ("09:00", "17:30"), "tuesday": ("09:00", "17:30"), "wednesday": ("09:00", "17:30"),
    "thursday": ("09:00", "19:00"), "friday": ("09:00", "17:30"), "saturday": ("10:00", "14:00"),
}

_booked: dict[tuple[date, str], dict] = {}       # (day, "HH:MM") -> who
_counter = [1000]


def hours(day: str = "") -> dict:
    """Opening times, for one named day or for the week."""
    key = (day or "").strip().lower()
    if key:
        if key not in HOURS:
            return {"day": key, "open": False, "say": f"We are closed on {key.title()}."}
        start, end = HOURS[key]
        return {"day": key, "open": True, "from": start, "to": end}
    return {"business": NAME, "week": {d: f"{a} to {b}" for d, (a, b) in HOURS.items()},
            "closed": ["sunday"]}


def free_times(day: date) -> list[str]:
    """Every slot that day which is inside opening hours, still in the future, and not taken."""
    weekday = day.strftime("%A").lower()
    if weekday not in HOURS:
        return []
    start, end = (time.fromisoformat(t) for t in HOURS[weekday])
    now = _now()
    out = []
    at = _combine(day, start)
    while _combine(day, end) > at:
        label = at.strftime("%H:%M")
        if at > now and (day, label) not in _booked:
            out.append(label)
        at += timedelta(minutes=SLOT_MINUTES)
    return out


def book(day: date, when: str, name: str, phone: str) -> str:
    _counter[0] += 1
    ref = f"B{_counter[0]}"
    _booked[(day, when)] = {"name": name, "phone": phone, "reference": ref}
    return ref


def bookings() -> list[dict]:
    return [{"date": d.isoformat(), "time": t, **who} for (d, t), who in sorted(_booked.items())]


# -- so that tests can hold the clock still --------------------------------------------------
_fixed = [None]


def _now():
    return _fixed[0] or __import__("datetime").datetime.now()


def _combine(day: date, at: time):
    return __import__("datetime").datetime.combine(day, at)
