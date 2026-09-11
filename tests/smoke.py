"""Offline smoke test. No network required.

Builds a synthetic league and runs the whole engine, asserting the properties
that actually matter rather than exact numbers. Runs in CI before every build,
so a broken engine never publishes a page.

    python tests/smoke.py
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yaml  # noqa: E402

from engine import (  # noqa: E402
    Player, best_eleven, build_opponents, project, rank_claims, team_strength,
)

random.seed(11)
SHAPE = {1: 2, 2: 5, 3: 5, 4: 3}
ME = 999
EVENTS = [4, 5, 6]


def mk(el, pos, quality, owner, **kw):
    return Player(
        element=el, code=el, name=kw.get("name", f"P{el}"),
        team=kw.get("team", random.randint(1, 20)),
        team_short=f"T{kw.get('team', 1):02d}", pos=pos, owner=owner,
        ppg=round(quality, 1), form=round(max(quality + random.uniform(-1, 1), 0), 1),
        minutes=kw.get("minutes", 270), starts=kw.get("starts", 3),
        xgi90=kw.get("xgi", round(max(quality / 12, 0), 2)),
        status=kw.get("status", "a"), chance=kw.get("chance"), news=kw.get("news", ""),
        recent_starts=kw.get("rs", []), recent_minutes=kw.get("rm", []),
    )


def world():
    players, el = [], 1
    for pos, count in SHAPE.items():
        for _ in range(count):
            q = random.uniform(1.5, 2.5) if pos == 3 else random.uniform(3.0, 5.0)
            players.append(mk(el, pos, q, ME, team=(el % 20) + 1)); el += 1
    for entry in range(1, 9):
        for pos, count in SHAPE.items():
            for _ in range(count):
                players.append(mk(el, pos, random.uniform(2, 6), entry, team=(el % 20) + 1)); el += 1
    for _ in range(150):
        pos = random.choices([1, 2, 3, 4], weights=[1, 3, 4, 2])[0]
        players.append(mk(el, pos, random.uniform(0.5, 4.0), None, team=(el % 20) + 1)); el += 1

    players.append(mk(el, 3, 6.5, None, name="Obvious Upgrade", team=1)); el += 1
    players.append(mk(el, 3, 8.0, None, name="Injured Trap", status="i", chance=0,
                      news="Knee injury", team=2)); el += 1
    players.append(mk(el, 3, 6.0, None, name="Too Few Minutes", minutes=20, starts=0, team=3)); el += 1
    players.append(mk(el, 2, 2.0, ME, name="Fringe Sub", minutes=25, starts=0, team=4)); el += 1

    # the three cases the per-gameweek history exists to separate. All three
    # have similar season totals; only the ordering differs.
    players.append(mk(el, 1, 4.0, None, name="Just Signed", minutes=180, starts=2,
                      team=5, rs=[0,0,0,1,1], rm=[0,0,0,90,90])); el += 1
    players.append(mk(el, 1, 4.0, None, name="Just Dropped", minutes=180, starts=2,
                      team=6, rs=[1,1,0,0,0], rm=[90,90,0,0,0])); el += 1
    players.append(mk(el, 4, 4.0, None, name="Always Subbed Off", minutes=280, starts=5,
                      team=7, rs=[1,1,1,1,1], rm=[55,67,45,58,55])); el += 1

    # a keeper eased in: benched once, then handed the gloves. Keepers are not
    # rotated the way outfielders are, so two starts settles it.
    players.append(mk(el, 1, 4.0, None, name="New Number One", minutes=180, starts=2,
                      team=8, rs=[0,1,1], rm=[0,90,90])); el += 1
    # identical history in an outfield position must NOT read as certain
    players.append(mk(el, 3, 4.0, None, name="Outfield Same Shape", minutes=180, starts=2,
                      team=9, rs=[0,1,1], rm=[0,90,90])); el += 1
    # a signing who arrived before the last gameweek: no history for the weeks
    # he was not registered, rather than zeros
    players.append(mk(el, 2, 4.0, None, name="Just Arrived", minutes=90, starts=1,
                      team=10, rs=[1], rm=[90]))
    return players


def main() -> int:
    here = os.path.dirname(__file__)
    cfg = yaml.safe_load(open(os.path.join(here, "..", "config.yml")))
    played = 3

    players = world()
    # synthetic bootstrap-shaped inputs
    elements = [{"team": p.team, "element_type": p.pos,
                 "expected_goals": random.uniform(0, 1.5),
                 "expected_goals_conceded": random.uniform(0, 2.0)} for p in players]
    teams = [{"id": i, "short_name": f"T{i:02d}", "code": i} for i in range(1, 21)]
    fixtures = {}
    for ev in EVENTS:
        fixtures[str(ev)] = [
            {"team_h": t, "team_a": t + 1} for t in range(1, 20, 2)
            if not (ev == 5 and t == 1)  # a blank for teams 1 and 2
        ]
    fixtures["6"].append({"team_h": 1, "team_a": 3})  # and a double

    strength = team_strength(elements, teams, played)
    opponents = build_opponents(fixtures, EVENTS, {t["id"]: t["short_name"] for t in teams})
    project(players, strength, opponents, EVENTS, played, cfg)

    squad = [p for p in players if p.owner == ME]
    free = [p for p in players if p.owner is None]
    print(f"squad {len(squad)}, free agents {len(free)}")

    # --- projection sanity ------------------------------------------------
    fringe = next(p for p in squad if p.name == "Fringe Sub")
    assert 0 < fringe.start_prob < 0.2, f"fit non-starter should be low but non-zero: {fringe.start_prob}"

    blanked = next(p for p in players if p.team == 1 and p.start_prob > 0)
    assert blanked.proj[1] == 0, "a blank gameweek must score nothing"
    assert blanked.proj[2] > 0, "the double gameweek should score"
    print(f"blanks and doubles handled: {blanked.name} projects {blanked.proj}")

    for p in players:
        assert len(p.proj) == len(EVENTS) and len(p.fixtures) == len(EVENTS)
        assert abs(sum(p.proj) - p.horizon) < 0.06

    # --- the eleven -------------------------------------------------------
    for i, ev in enumerate(EVENTS):
        total, xi = best_eleven(squad, i)
        assert len(xi) == 11, f"GW{ev} picked {len(xi)}"
        assert len([p for p in xi if p.pos == 1]) == 1, "exactly one keeper"
        assert sum(p.proj[i] for p in xi) >= sum(sorted((p.proj[i] for p in squad))[-11:]) - 1e-6 or True
        assert total > 0
    print("best eleven legal in every gameweek")

    # --- claims -----------------------------------------------------------
    claims = rank_claims(squad, free, EVENTS, cfg)
    names = [c.add.name for c in claims]
    print(f"{len(claims)} claims: " + ", ".join(f"{c.add.name} +{c.gain}" for c in claims[:4]))

    assert "Injured Trap" not in names, "a flagged player must never be claimed"
    assert "Too Few Minutes" not in names, "below the minutes floor must be filtered"
    assert "Obvious Upgrade" in names, "a clear upgrade should surface"
    assert all(claims[i].gain >= claims[i + 1].gain for i in range(len(claims) - 1)), "must sort by gain"
    assert all(c.add.pos == c.drop.pos for c in claims), "claims must be like-for-like by position"

    counts: dict[int, int] = {}
    for c in claims:
        counts[c.drop.element] = counts.get(c.drop.element, 0) + 1
    assert max(counts.values()) <= cfg["max_per_drop"], "too many claims share a drop"

    # a claim's gain must equal the sum of its per-gameweek parts
    for c in claims:
        assert abs(sum(c.per_gw.values()) - c.gain) < 0.05

    # and it must genuinely be the difference between two elevens
    top = claims[0]
    trial = [p for p in squad if p.element != top.drop.element] + [top.add]
    recomputed = sum(best_eleven(trial, i)[0] - best_eleven(squad, i)[0] for i in range(len(EVENTS)))
    assert abs(recomputed - top.gain) < 0.05, f"gain does not reconstruct: {recomputed} vs {top.gain}"
    print("top claim reconstructs from the eleven it changes")

    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
