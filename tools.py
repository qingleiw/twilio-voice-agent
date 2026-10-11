"""What the model is allowed to do, and the rules it cannot talk its way around.

Two of these read from a stand-in business (`shop.py`) that a real deployment would replace with a
booking system or a CRM. The other two end the call. Everything a caller is told about opening
times or availability comes from here, so the model cannot invent one; and the rules that matter -
no slot that is not free, no transfer to a number the business did not configure - are enforced in
this file rather than asked for in the prompt, because a prompt is a request and this is not.
"""
import os
import re
from datetime import date, datetime, timedelta

import shop

RECEPTION = os.getenv("RECEPTION_NUMBER", "")

SCHEMA = [{"function_declarations": [
    {"name": "opening_hours",
     "description": "When the business is open. Call this before telling a caller any time.",
     "parameters": {"type": "object", "properties": {
         "day": {"type": "string", "description": "A weekday name, or empty for the whole week"}}}},
    {"name": "check_availability",
     "description": "Free appointment times on one date. Dates are YYYY-MM-DD.",
     "parameters": {"type": "object", "properties": {"date": {"type": "string"}},
                    "required": ["date"]}},
    {"name": "book",
     "description": "Take a booking, once the caller has agreed to a time the tool offered.",
     "parameters": {"type": "object", "properties": {
         "date": {"type": "string"}, "time": {"type": "string", "description": "HH:MM, 24 hour"},
         "name": {"type": "string"}, "phone": {"type": "string", "description": "Only if the caller said it"}},
         "required": ["date", "time", "name"]}},
    {"name": "transfer_call", "description": "Put the caller through to a person.",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}}},
    {"name": "end_call", "description": "Hang up. Say goodbye first.",
     "parameters": {"type": "object", "properties": {}}},
]}]


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def _said_it(state: dict, phone: str) -> bool:
    """True if those digits actually appeared in what the caller said.

    A model asked for a phone number will sometimes produce a plausible one. The transcript is the
    only evidence that a caller gave it, so a number that is not in the transcript is dropped.
    """
    digits = re.sub(r"\D", "", phone or "")
    heard = re.sub(r"\D", "", state.get("heard", ""))
    return bool(digits) and digits[-6:] in heard


def run(name: str, args: dict, state: dict, finish) -> dict:
    if name == "opening_hours":
        return shop.hours(args.get("day", ""))

    if name == "check_availability":
        day = _parse_day(args.get("date", ""))
        if not day:
            return {"error": "date must be YYYY-MM-DD"}
        if day < date.today():
            return {"status": "past", "say": "That date has already passed. What day would suit you?"}
        if day > date.today() + timedelta(days=90):
            return {"status": "too_far", "say": "I can only book up to three months ahead."}
        times = shop.free_times(day)
        return {"date": day.isoformat(), "times": times} if times else \
            {"date": day.isoformat(), "status": "full", "say": "There is nothing left that day."}

    if name == "book":
        day = _parse_day(args.get("date", ""))
        when = (args.get("time") or "").strip()
        if not day or when not in shop.free_times(day):
            # the model has drifted from what it was offered; make it ask again rather than confirm
            return {"status": "not_available",
                    "say": "I am sorry, that time has just gone. Shall I read you what is left?"}
        phone = args.get("phone", "")
        if phone and not _said_it(state, phone):
            phone = ""
        ref = shop.book(day, when, args.get("name", "").strip(), phone or state.get("caller", ""))
        return {"status": "booked", "reference": ref, "date": day.isoformat(), "time": when,
                "say": f"You are booked for {when} on {day:%A the %drd of %B}. Your reference is {ref}."}

    if name == "transfer_call":
        if not RECEPTION:
            return {"status": "no_one_available",
                    "say": "There is no one free to take the call right now. Can I take a message?"}
        state["closing"] = True
        finish("transfer", RECEPTION)
        return {"status": "transferring", "say": "I will put you through now. One moment."}

    if name == "end_call":
        state["closing"] = True
        finish("hangup")
        return {"status": "ok"}

    return {"error": f"no such tool: {name}"}
