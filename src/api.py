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
