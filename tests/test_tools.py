"""The rules the model is not allowed to talk its way around. No network, no API key."""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import shop  # noqa: E402
import tools  # noqa: E402

_today = datetime.date.today()
MONDAY = _today + datetime.timedelta(days=(7 - _today.weekday()) % 7 or 7)   # the next Monday


def setup_function():
    shop._booked.clear()
    shop._fixed[0] = datetime.datetime.combine(MONDAY, datetime.time(8, 0))


def call(name, args, state=None):
    done = []
    out = tools.run(name, args, state if state is not None else {}, lambda *a: done.append(a))
    return out, done


def test_opening_hours_come_from_the_business_not_the_model():
    out, _ = call("opening_hours", {"day": "thursday"})
    assert out == {"day": "thursday", "open": True, "from": "09:00", "to": "19:00"}
    assert call("opening_hours", {"day": "sunday"})[0]["open"] is False


def test_a_date_in_the_past_is_refused():
    out, _ = call("check_availability", {"date": "2020-01-01"})
    assert out["status"] == "past"


def test_availability_lists_real_slots_and_skips_what_is_taken():
    out, _ = call("check_availability", {"date": MONDAY.isoformat()})
    assert out["times"][0] == "09:00" and "17:00" in out["times"]
    shop.book(MONDAY, "09:00", "someone", "")
    assert "09:00" not in call("check_availability", {"date": MONDAY.isoformat()})[0]["times"]


def test_booking_a_time_that_was_never_offered_is_refused():
    """The model sometimes confirms a time it invented. The booking has to come from the diary."""
    out, _ = call("book", {"date": MONDAY.isoformat(), "time": "03:00", "name": "Alex"})
    assert out["status"] == "not_available" and not shop.bookings()


def test_booking_a_free_slot_returns_a_reference():
    out, _ = call("book", {"date": MONDAY.isoformat(), "time": "10:00", "name": "Alex"})
    assert out["status"] == "booked" and out["reference"].startswith("B")
    assert shop.bookings()[0]["name"] == "Alex"
    assert "10:00" not in call("check_availability", {"date": MONDAY.isoformat()})[0]["times"]


def test_a_phone_number_the_caller_never_said_is_dropped():
    state = {"heard": "my name is Alex", "caller": "+61400111222"}
    call("book", {"date": MONDAY.isoformat(), "time": "11:00", "name": "Alex",
                  "phone": "0499888777"}, state)
    assert shop.bookings()[0]["phone"] == "+61400111222"     # the caller ID, not the invented one


def test_a_phone_number_the_caller_did_say_is_kept():
    state = {"heard": "it is oh four nine nine 888 777", "caller": "+61400111222"}
    call("book", {"date": MONDAY.isoformat(), "time": "11:30", "name": "Alex",
                  "phone": "0499888777"}, state)
    assert shop.bookings()[0]["phone"] == "0499888777"


def test_transfer_without_a_configured_number_does_not_pretend():
    tools.RECEPTION = ""
    out, done = call("transfer_call", {"reason": "complaint"})
    assert out["status"] == "no_one_available" and not done


def test_transfer_hands_the_call_to_the_configured_number():
    tools.RECEPTION = "+61390001111"
    state = {}
    out, done = call("transfer_call", {"reason": "complaint"}, state)
    assert out["status"] == "transferring" and done == [("transfer", "+61390001111")]
    assert state["closing"] is True


def test_end_call_closes_the_line():
    state = {}
    out, done = call("end_call", {}, state)
    assert out["status"] == "ok" and done == [("hangup",)] and state["closing"] is True
