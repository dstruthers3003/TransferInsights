"""Fetch, compute, and write the published page.

Outputs two files into docs/:

    index.html    the app, with this run's numbers baked in
    waivers.json  the same numbers on their own

The page prefers the JSON when it can reach it, so a browser tab left open
picks up a new run without a reload of the markup. The baked-in copy is what
makes the file work when saved to a phone and opened offline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import yaml

import api
from engine import (
    Claim, Player, best_eleven, build_opponents, pick_eleven, project,
    rank_claims, team_strength,
)


def load_config(path: str) -> dict:
    with open(path) as fh:
        cfg = yaml.safe_load(fh)
    for key in ("league_id", "entry_id"):
        env = os.environ.get(f"FPL_{key.upper()}")
        if env:
            cfg[key] = int(env)
    return cfg


def build_players(boot: dict, status: dict, played: int) -> list[Player]:
    short_of = {t["id"]: t["short_name"] for t in boot["teams"]}
    owners = {e["element"]: e["owner"] for e in status["element_status"]}

    players: list[Player] = []
    for e in boot["elements"]:
        minutes = e.get("minutes") or 0
        per90 = minutes / 90.0
        xgi = float(e.get("expected_goal_involvements") or 0)
        players.append(
            Player(
                element=e["id"],
                code=e.get("code", 0),
                name=e["web_name"],
                team=e["team"],
                team_short=short_of.get(e["team"], "?"),
                pos=e["element_type"],
                owner=owners.get(e["id"]),
                ppg=float(e.get("points_per_game") or 0),
                form=float(e.get("form") or 0),
                minutes=minutes,
                starts=e.get("starts") or 0,
                xgi90=round(xgi / per90, 2) if per90 else 0.0,
                status=e.get("status", "a"),
                chance=e.get("chance_of_playing_next_round"),
                news=(e.get("news") or "")[:90],
            )
        )
    return players


def slim(p: Player, with_fixtures: bool = True) -> dict:
    out = {
        "n": p.name, "tn": p.team_short, "pos": p.pos,
        "mp": p.start_prob, "rate": p.rate, "mins": p.minutes,
        "starts": p.starts, "ppg": p.ppg, "form": p.form,
        "xgi": p.xgi90, "h": p.horizon, "news": p.news,
        "proj": p.proj,
    }
    if with_fixtures:
        out["fx"] = p.fixtures
    return out


def opponent_schedule(details: dict, entry_id: int, events: list[int]) -> dict:
    """Who you play each gameweek, keyed by event.

    League fixtures reference `league_entry` ids, which are not the same as
    the `entry_id` used for ownership - so the two have to be mapped through
    league_entries or the wrong squad gets shown.

    A league with an odd number of managers carries a dummy AVERAGE side so
    everyone gets a fixture. It has no entry_id and no squad, and is handled
    separately.
    """
    entries = details.get("league_entries", [])
    by_le = {e["id"]: e for e in entries}
    mine = next((e["id"] for e in entries if e.get("entry_id") == entry_id), None)
    if mine is None:
        return {}

    out = {}
    for ev in events:
        match = next(
            (m for m in details.get("matches", [])
             if m.get("event") == ev and mine in (m.get("league_entry_1"), m.get("league_entry_2"))),
            None,
        )
        if not match:
            out[ev] = None
            continue
        other = match["league_entry_2"] if match["league_entry_1"] == mine else match["league_entry_1"]
        e = by_le.get(other, {})
        out[ev] = {
            "entry": e.get("entry_id"),
            "name": e.get("entry_name") or "AVERAGE",
            "manager": f"{e.get('player_first_name','')} {e.get('player_last_name','')}".strip(),
            "average": not e.get("entry_id"),
        }
    return out


def build_opponent_views(details: dict, players: list[Player], events: list[int],
                         schedule: dict, my_totals: dict) -> list[dict]:
    """Each gameweek's opponent, their eleven, and the projected margin.

    AVERAGE has no squad. Its score is modelled as the mean projected eleven
    across every real manager in the league, which is the closest honest
    reading of what the dummy side represents.
    """
    real = [e for e in details.get("league_entries", []) if e.get("entry_id")]
    squads = {e["entry_id"]: [p for p in players if p.owner == e["entry_id"]] for e in real}

    league_mean = {}
    for i, ev in enumerate(events):
        totals = [pick_eleven(sq, i)["total"] for sq in squads.values() if sq]
        league_mean[ev] = round(sum(totals) / len(totals), 1) if totals else 0.0

    views = []
    for i, ev in enumerate(events):
        opp = schedule.get(ev)
        mine = my_totals.get(str(ev), my_totals.get(ev, 0))
        if not opp:
            views.append({"ev": ev, "none": True, "mine": mine})
            continue

        if opp["average"]:
            theirs = league_mean[ev]
            views.append({
                "ev": ev, "name": "AVERAGE", "manager": "", "average": True,
                "proj": theirs, "mine": mine, "margin": round(mine - theirs, 1),
                "shape": None, "xi": [], "bench": [],
                "note": "the dummy side, scored as the league's mean eleven",
            })
            continue

        side = pick_eleven(squads.get(opp["entry"], []), i)
        views.append({
            "ev": ev, "name": opp["name"], "manager": opp["manager"], "average": False,
            "proj": side["total"], "mine": mine, "margin": round(mine - side["total"], 1),
            "shape": side["shape"],
            "xi": [{"n": p.name, "tn": p.team_short, "pos": p.pos, "mp": p.start_prob,
                    "proj": p.proj[i], "fx": p.fixtures[i]} for p in side["xi"]],
            "bench": [{"n": p.name, "tn": p.team_short, "pos": p.pos, "mp": p.start_prob,
                       "proj": p.proj[i], "fx": p.fixtures[i]} for p in side["bench"]],
        })
    return views


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--template", default="template/waiver-wire.html")
    ap.add_argument("--out-dir", default="docs")
    args = ap.parse_args()

    cfg = load_config(args.config)
    league_id, entry_id = cfg.get("league_id"), cfg.get("entry_id")
    if not league_id or not entry_id:
        print("league_id and entry_id must be set", file=sys.stderr)
        return 1

    boot = api.bootstrap()
    game = api.game()
    details = api.league_details(league_id)
    status = api.element_status(league_id)

    played = game.get("current_event") or 0
    events = api.fixture_events(boot)
    if not events:
        print("no fixtures published yet; nothing to build")
        return 0
    if cfg.get("horizon"):
        events = events[: cfg["horizon"]]

    players = build_players(boot, status, played)
    strength = team_strength(boot["elements"], boot["teams"], played)
    short_of = {t["id"]: t["short_name"] for t in boot["teams"]}
    opponents = build_opponents(boot.get("fixtures") or {}, events, short_of)
    project(players, strength, opponents, events, played, cfg)

    squad = [p for p in players if p.owner == entry_id]
    if len(squad) != sum([2, 5, 5, 3]):
        print(f"warning: squad has {len(squad)} players, expected 15", file=sys.stderr)
    if not squad:
        print(f"no players owned by entry {entry_id} in league {league_id}", file=sys.stderr)
        return 1

    free = [p for p in players if p.owner is None]
    claims = rank_claims(squad, free, events, cfg)

    base_gw = {str(ev): round(best_eleven(squad, i)[0], 1) for i, ev in enumerate(events)}
    baseline = round(sum(base_gw.values()), 1)

    me = next((e for e in details["league_entries"] if e.get("entry_id") == entry_id), {})
    deadline = next(
        (e.get("deadline_time") for e in api.events_list(boot) if e.get("id") == events[0]), None
    )
    owned = len([e for e in status["element_status"] if e["owner"] is not None])

    schedule = opponent_schedule(details, entry_id, events)
    opponents = build_opponent_views(details, players, events, schedule, base_gw)

    payload = {
        "v": 1,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "leagueId": league_id,
        "leagueName": details["league"]["name"],
        "entryId": entry_id,
        "teamName": me.get("entry_name") or "My team",
        "manager": f"{me.get('player_first_name','')} {me.get('player_last_name','')}".strip(),
        "gwPlayed": played,
        "gwFinished": bool(game.get("current_event_finished")),
        "nextEvent": game.get("next_event"),
        "EV": events,
        "deadline": deadline,
        "baseline": baseline,
        "baseGW": base_gw,
        "teamCodes": {t["short_name"]: t["code"] for t in boot["teams"]},
        "claims": [
            {
                "gain": c.gain,
                "per": {str(k): v for k, v in c.per_gw.items()},
                "add": slim(c.add),
                "drop": {"n": c.drop.name, "tn": c.drop.team_short, "pos": c.drop.pos,
                         "mp": c.drop.start_prob, "h": c.drop.horizon},
            }
            for c in claims
        ],
        "squad": sorted([slim(p) for p in squad], key=lambda d: -d["h"]),
        "opponents": opponents,
        "pool": {
            "total": len(status["element_status"]),
            "owned": owned,
            "free": len(status["element_status"]) - owned,
            "considered": len([p for p in free if p.minutes >= cfg["min_minutes"]]),
        },
    }

    os.makedirs(args.out_dir, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    with open(args.template) as fh:
        template = fh.read()
    if "__SEED__" not in template:
        print("template is missing the __SEED__ marker", file=sys.stderr)
        return 1

    with open(os.path.join(args.out_dir, "index.html"), "w") as fh:
        fh.write(template.replace("__SEED__", blob))
    with open(os.path.join(args.out_dir, "waivers.json"), "w") as fh:
        fh.write(blob)

    state = "final" if payload["gwFinished"] else "IN PROGRESS - numbers will move"
    print(f"GW{played} {state}, valuing GW{events[0]}-{events[-1]}")
    print(f"best eleven {baseline} over {len(events)} gameweeks")
    for i, c in enumerate(claims, 1):
        print(f"  {i}. +{c.gain:5.1f}  {c.add.name:<16} ({c.add.team_short}) for {c.drop.name}")
    for o in opponents:
        if o.get("none"):
            continue
        verdict = "ahead" if o["margin"] > 0 else "behind"
        print(f"  GW{o['ev']} v {o['name']}: {o['mine']} - {o['proj']} ({verdict} {abs(o['margin'])})")
    dead = [p for p in squad if p.start_prob < 0.35]
    if dead:
        print("dead weight: " + ", ".join(f"{p.name} {int(p.start_prob*100)}%" for p in dead))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
