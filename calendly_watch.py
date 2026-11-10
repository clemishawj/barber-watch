#!/usr/bin/env python3
"""Watch a Calendly page and email you when a slot earlier than your current
appointment opens up. Standard library only (Python 3.9+).

Setup:
  export GMAIL_USER="clemishawj97@gmail.com"
  export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"   # Google Account > Security > App passwords
  export CURRENT_APPOINTMENT="2026-11-28T14:30"      # your booked time, local time

Test:   python3 calendly_watch.py --test     (prints slots, sends nothing)
Run:    python3 calendly_watch.py
Cron:   */10 * * * * cd /path/to/folder && /usr/bin/python3 calendly_watch.py
"""
import json
import os
import smtplib
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

PROFILE_SLUG = "anthony-theadkbarbershop"
EVENT_SLUG = "30min"
TIMEZONE = "America/New_York"
EMAIL_TO = os.environ["EMAIL_TO"].strip()
DAYS_AHEAD = 60          # how far into the future to look
ALERT_AFTER_FAILURES = 6  # email you if this many runs in a row fail

BOOKING_URL = f"https://calendly.com/{PROFILE_SLUG}/{EVENT_SLUG}"
BASE = "https://calendly.com/api/booking/event_types"
STATE_FILE = Path(__file__).with_name("calendly_state.json")
TZ = ZoneInfo(TIMEZONE)


def get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (personal availability checker)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_slots():
    """Return a sorted list of timezone-aware datetimes for open slots."""
    q = urllib.parse.urlencode({
        "event_type_slug": EVENT_SLUG,
        "profile_slug": PROFILE_SLUG,
    })
    uuid = get_json(f"{BASE}/lookup?{q}")["uuid"]

    slots = set()
    start = date.today()
    end_limit = start + timedelta(days=DAYS_AHEAD)
    while start < end_limit:
        end = min(start + timedelta(days=30), end_limit)
        q = urllib.parse.urlencode({
            "timezone": TIMEZONE,
            "diagnostics": "false",
            "range_start": start.isoformat(),
            "range_end": end.isoformat(),
        })
        data = get_json(f"{BASE}/{uuid}/calendar/range?{q}")
        for day in data.get("days", []):
            for spot in day.get("spots", []):
                if spot.get("status", "available") == "available":
                    slots.add(datetime.fromisoformat(spot["start_time"]))
        start = end
    return sorted(slots)


def send_email(subject, body):
    user = os.environ["GMAIL_USER"].strip()
    password = "".join(os.environ["GMAIL_APP_PASSWORD"].split())
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, password)
        s.send_message(msg)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"notified": [], "failures": 0}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fmt(dt):
    return dt.astimezone(TZ).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def main():
    test = "--test" in sys.argv
    state = load_state()

    try:
        slots = fetch_slots()
    except Exception as exc:
        state["failures"] = state.get("failures", 0) + 1
        save_state(state)
        print(f"Fetch failed ({state['failures']} in a row): {exc}")
        if state["failures"] == ALERT_AFTER_FAILURES and not test:
            send_email("Barber watcher is failing",
                       f"The last {ALERT_AFTER_FAILURES} checks failed.\nLatest error: {exc}\n"
                       "Calendly may have changed something.")
        return
    state["failures"] = 0

    current = os.environ.get("CURRENT_APPOINTMENT")
    if current:
        cutoff = datetime.fromisoformat(current).replace(tzinfo=TZ)
        earlier = [s for s in slots if s < cutoff]
    else:
        earlier = slots  # no appointment set: report everything

    print(f"{len(slots)} open slots found, {len(earlier)} earlier than your appointment.")
    if test:
        for s in slots[:15]:
            print("  ", fmt(s))
        return

    notified = set(state.get("notified", []))
    new = [s for s in earlier if s.isoformat() not in notified]
    if new:
        lines = "\n".join(f"  - {fmt(s)}" for s in new[:20])
        send_email(f"Earlier barber slot open: {fmt(new[0])}",
                   f"These earlier times just opened up:\n\n{lines}\n\nBook here: {BOOKING_URL}\n")
        notified.update(s.isoformat() for s in new)

    # Forget slots that are now in the past so the file stays small.
    now = datetime.now(TZ)
    state["notified"] = [n for n in notified if datetime.fromisoformat(n) > now]
    save_state(state)


if __name__ == "__main__":
    main()
