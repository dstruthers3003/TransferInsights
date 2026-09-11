"""Read-only client for the public FPL Draft API.

Every endpoint used here answers without a session. That was verified by
calling each one with cookies suppressed, which is the same position a CI
runner is in. Nothing in this module sends a credential, and nothing writes
back to the game.

The draft origin carries everything needed - player stats including expected
goals, ownership, and a rolling fixture window - so there is no second host
and no CORS problem to work around.
"""

from __future__ import annotations

import time
from typing import Any

import requests

BASE = "https://draft.premierleague.com/api"
HEADERS = {"User-Agent": "fpl-waiver-wire (github actions; personal use)"}


def _get(path: str, retries: int = 3, backoff: float = 2.0) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(f"{BASE}/{path}", headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"failed to fetch {path}: {last}")


def bootstrap() -> dict:
    """Players, teams, and fixtures for the next few gameweeks."""
    return _get("bootstrap-static")


def game() -> dict:
    """Which gameweek is current, which is next, whether it has finished."""
    return _get("game")


def league_details(league_id: int) -> dict:
    return _get(f"league/{league_id}/details")


def element_status(league_id: int) -> dict:
    """Ownership across the league. owner is an entry id, or None if free."""
    return _get(f"league/{league_id}/element-status")


def event_live(event: int) -> dict[int, dict]:
    """Minutes and starts for every player in one gameweek.

    One call covers the whole league, which is what makes per-gameweek history
    affordable: a five-gameweek window costs five requests, not one per player.
    """
    data = _get(f"event/{event}/live")
    out: dict[int, dict] = {}
    for pid, payload in (data.get("elements") or {}).items():
        stats = payload.get("stats") or {}
        out[int(pid)] = {
            "minutes": stats.get("minutes", 0) or 0,
            "starts": stats.get("starts", 0) or 0,
        }
    return out


def recent_form(upto_event: int, window: int) -> dict[int, dict]:
    """Per-player minutes and starts over the last `window` gameweeks.

    Ordered oldest to newest so the caller can weight the tail more heavily.
    A gameweek that errors is skipped rather than failing the run - a shorter
    history degrades the estimate without breaking it.

    Players are only recorded for gameweeks they actually appear in. The live
    payload grows as the season goes on, because players are added when they
    are registered, so a mid-season signing is simply absent from earlier
    gameweeks. Recording those as zeros would mark him down for games he could
    not have played; leaving them out judges him on the ones he could.
    """
    first = max(upto_event - window + 1, 1)
    history: dict[int, dict] = {}
    for ev in range(first, upto_event + 1):
        try:
            live = event_live(ev)
        except RuntimeError:
            continue
        for pid, rec in live.items():
            slot = history.setdefault(pid, {"minutes": [], "starts": []})
            slot["minutes"].append(rec["minutes"])
            slot["starts"].append(rec["starts"])
    return history


def fixture_events(bootstrap_data: dict) -> list[int]:
    """Gameweeks with fixtures published, oldest first.

    The API exposes a rolling window rather than the whole season, so this is
    typically three. Everything downstream is valued over whatever it gives.
    """
    fixtures = bootstrap_data.get("fixtures") or {}
    return sorted(int(k) for k in fixtures.keys())


def events_list(bootstrap_data: dict) -> list[dict]:
    """The events payload is an object with a `data` list, not a bare list."""
    ev = bootstrap_data.get("events") or {}
    if isinstance(ev, dict):
        return ev.get("data") or []
    return ev if isinstance(ev, list) else []
