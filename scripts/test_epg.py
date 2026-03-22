#!/usr/bin/env python3
"""Diagnostic CLI: test EPG data availability from the TVHeadend server.

Run with:
    uv run python scripts/test_epg.py

Each numbered step is independent.  A failure in one step will stop
further tests that depend on it, but unrelated tests still run.
"""

from __future__ import annotations

import configparser
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
INFO = "\033[34m→\033[0m"

CONFIG_PATH = Path.home() / ".config" / "tvhgtk" / "config"


def hdr(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m")


def ok(text: str) -> None:
    print(f"  {PASS}  {text}")


def fail(text: str) -> None:
    print(f"  {FAIL}  {text}")


def info(text: str) -> None:
    print(f"  {INFO}  {text}")


# ---------------------------------------------------------------------------
# Step 1 – config file
# ---------------------------------------------------------------------------
hdr("Step 1: Read config file")
if not CONFIG_PATH.exists():
    fail(f"Config file not found: {CONFIG_PATH}")
    sys.exit(1)
ok(f"Found config file: {CONFIG_PATH}")

parser = configparser.ConfigParser()
parser.read(CONFIG_PATH)

if "server" not in parser:
    fail("Missing [server] section in config")
    sys.exit(1)

server = parser["server"]
raw_url = server.get("url", "").strip()
username = server.get("username", "").strip()
password = server.get("password", "").strip()

missing = [
    k
    for k, v in [("url", raw_url), ("username", username), ("password", password)]
    if not v
]
if missing:
    fail(f"Missing config values: {', '.join(missing)}")
    sys.exit(1)

parsed_url = urlparse(raw_url)
ok(f"URL:      {raw_url}")
ok(f"Scheme:   {parsed_url.scheme}")
ok(f"Host:     {parsed_url.hostname}")
ok(f"Port:     {parsed_url.port or '(default)'}")
ok(f"Username: {username}")
ok(f"Password: {'*' * len(password)}")

# ---------------------------------------------------------------------------
# Step 2 – configure tvheadend client
# ---------------------------------------------------------------------------
hdr("Step 2: Configure tvheadend client")
try:
    from tvheadend import configure

    configure(
        host=parsed_url.hostname or "",
        username=username,
        password=password,
        scheme=parsed_url.scheme or "http",
        port=parsed_url.port,
    )
    ok("tvheadend client configured")
except Exception as exc:
    fail(f"Failed to configure client: {exc}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Step 3 – fetch channels
# ---------------------------------------------------------------------------
hdr("Step 3: Fetch channel list")
try:
    from tvheadend import channelGrid

    channels, total = channelGrid()
    ok(f"Received {len(channels)} channels (server reports total={total})")
    if not channels:
        fail("Channel list is empty — cannot test EPG per-channel")
        sys.exit(1)
    for ch in sorted(
        channels, key=lambda c: (c.get("number", 99999), c.get("name", ""))
    ):
        info(
            f"  ch {ch.get('number', '-'):>5}  {ch.get('name', '<unnamed>')}  ({ch.get('uuid', '<no-uuid>')})"
        )
except Exception as exc:
    fail(f"channelGrid() raised: {exc}")
    sys.exit(1)

first_channel = channels[0]
first_uuid = str(first_channel.get("uuid", "")).strip()
first_name = str(first_channel.get("name", "<unnamed>"))

# ---------------------------------------------------------------------------
# Step 4 – raw EPG endpoint with no filters
# ---------------------------------------------------------------------------
hdr("Step 4: Raw EPG — no filters (limit=5)")
try:
    from tvheadend import epgEvents

    events, total_epg = epgEvents(limit=5)
    if events:
        ok(f"Received {len(events)} event(s) (server reports total={total_epg})")
        for ev in events:
            ts = ev.get("start")
            dt = datetime.fromtimestamp(ts).isoformat() if isinstance(ts, int) else "?"
            info(
                f"  {dt}  {ev.get('title', '<no title>')}  ch={ev.get('channelUuid', '?')}"
            )
    else:
        fail(
            f"No EPG events returned at all (total field={total_epg}). "
            "EPG may not be configured on the server."
        )
except Exception as exc:
    fail(f"epgEvents() raised: {exc}")

# ---------------------------------------------------------------------------
# Step 5 – EPG for first channel, no time window
# ---------------------------------------------------------------------------
hdr(f"Step 5: EPG for first channel without time filter  ({first_name})")
try:
    from tvheadend import epgEventsOnChannel

    events5, total5 = epgEventsOnChannel(first_uuid, limit=5)
    if events5:
        ok(f"Received {len(events5)} event(s) (total={total5})")
        for ev in events5:
            ts = ev.get("start")
            dt = datetime.fromtimestamp(ts).isoformat() if isinstance(ts, int) else "?"
            info(f"  {dt}  {ev.get('title', '<no title>')}")
    else:
        fail(
            f"No EPG events for uuid={first_uuid} without time filter (total={total5})"
        )
except Exception as exc:
    fail(f"epgEventsOnChannel() raised: {exc}")

# ---------------------------------------------------------------------------
# Step 6 – EPG for first channel WITH the 24h window the app uses
# ---------------------------------------------------------------------------
now = int(time.time())
window_end = now + 24 * 60 * 60
hdr(f"Step 6: EPG for first channel WITH 24h window  ({first_name})")
info(f"now        = {now}  ({datetime.fromtimestamp(now).isoformat()})")
info(f"window_end = {window_end}  ({datetime.fromtimestamp(window_end).isoformat()})")
try:
    events6, total6 = epgEventsOnChannel(first_uuid, start=now, stop=window_end)
    if events6:
        ok(f"Received {len(events6)} event(s) (total={total6})")
        for ev in sorted(events6, key=lambda e: e.get("start", 0)):
            ts = ev.get("start")
            dt = datetime.fromtimestamp(ts).isoformat() if isinstance(ts, int) else "?"
            info(f"  {dt}  {ev.get('title', '<no title>')}")
    else:
        fail(
            f"No EPG events in the 24h window (total={total6}).\n"
            "   This is what the app queries — if step 5 returned events,\n"
            "   the server may be interpreting start/stop differently:\n"
            f"   start={now}, stop={window_end}"
        )
except Exception as exc:
    fail(f"epgEventsOnChannel(start=..., stop=...) raised: {exc}")

# ---------------------------------------------------------------------------
# Step 7 – dump raw JSON for first channel EPG (no time filter)
# ---------------------------------------------------------------------------
hdr("Step 7: Raw JSON dump for first channel (first event, no time filter)")
try:
    from tvheadend.config import getConfig
    from tvheadend.tvh import send_to_tvh

    cfg = getConfig()
    raw = send_to_tvh(
        cfg, "epg/events/grid", data={"limit": 1, "channelUuid": first_uuid}
    )
    print(json.dumps(raw, indent=2))
except Exception as exc:
    fail(f"Raw API call raised: {exc}")

# ---------------------------------------------------------------------------
# FINDINGS
# ---------------------------------------------------------------------------
# TVHeadend's EPG grid API uses event IDs, NOT Unix timestamps, for the
# 'start' and 'stop' params.  The correct time-window params are:
#   startsAfter  – Unix timestamp; return events starting after this time
#   startsBefore – Unix timestamp; return events starting before this time
# Passing large Unix timestamps as 'start'/'stop' returns zero results
# because no event ID is that large.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Step 8 – correct time filter: startsAfter / startsBefore
# ---------------------------------------------------------------------------
hdr("Step 8: Correct time filter — startsAfter / startsBefore (24h)")
try:
    now = int(time.time())
    window_end = now + 24 * 60 * 60
    info(f"now={now}, window_end={window_end}")
    raw8 = send_to_tvh(
        cfg,
        "epg/events/grid",
        {
            "limit": 5,
            "channelUuid": first_uuid,
            "startsAfter": now,
            "startsBefore": window_end,
        },
    )
    entries8 = raw8.get("entries", [])
    if entries8:
        ok(f"Received {len(entries8)} event(s) (total={raw8.get('totalCount')})")
        for e in entries8:
            ts = e.get("start")
            dt = datetime.fromtimestamp(ts).isoformat() if isinstance(ts, int) else "?"
            info(f"  {dt}  {e.get('channelName')}  {e.get('title')}")
    else:
        fail(
            f"No events returned with startsAfter/startsBefore (total={raw8.get('totalCount')})"
        )
except Exception as exc:
    fail(f"Raw API call raised: {exc}")

# ---------------------------------------------------------------------------
# Step 9 – test 'channel' as the per-channel filter param name
# ---------------------------------------------------------------------------
hdr("Step 9: Test 'channel' param as channel filter (instead of 'channelUuid')")
try:
    raw9 = send_to_tvh(
        cfg,
        "epg/events/grid",
        {
            "limit": 5,
            "channel": first_uuid,
            "startsAfter": int(time.time()),
            "startsBefore": int(time.time()) + 24 * 3600,
        },
    )
    entries9 = raw9.get("entries", [])
    if entries9:
        channel_names = {e.get("channelName") for e in entries9}
        ok(
            f"Received {len(entries9)} event(s) (total={raw9.get('totalCount')}) — channels: {channel_names}"
        )
        if channel_names == {first_name}:
            ok("'channel' param correctly filters to a single channel")
        else:
            info("'channel' param did not filter to a single channel")
    else:
        fail(
            f"No events (total={raw9.get('totalCount')}) — 'channel' param may not be valid"
        )
except Exception as exc:
    fail(f"Raw API call raised: {exc}")

# ---------------------------------------------------------------------------
# Step 10 – test channelUuid with startsAfter/startsBefore
# ---------------------------------------------------------------------------
hdr(
    "Step 10: Test 'channelUuid' with startsAfter/startsBefore — confirm channel filter works"
)
try:
    raw10 = send_to_tvh(
        cfg,
        "epg/events/grid",
        {
            "limit": 5,
            "channelUuid": first_uuid,
            "startsAfter": int(time.time()),
            "startsBefore": int(time.time()) + 24 * 3600,
        },
    )
    entries10 = raw10.get("entries", [])
    if entries10:
        channel_names = {e.get("channelName") for e in entries10}
        ok(
            f"Received {len(entries10)} event(s) (total={raw10.get('totalCount')}) — channels: {channel_names}"
        )
        if channel_names == {first_name}:
            ok("'channelUuid' param correctly filters to a single channel")
        else:
            info(
                f"Results span multiple channels — 'channelUuid' may not filter correctly"
            )
    else:
        fail(f"No events (total={raw10.get('totalCount')})")
except Exception as exc:
    fail(f"Raw API call raised: {exc}")

print()
