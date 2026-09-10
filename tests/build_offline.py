"""Offline test of the build plumbing: template injection and payload shape.

Stubs the API with a synthetic league so the whole of build.py runs without a
network. Catches the failures a pure engine test cannot: a missing template
marker, a payload the page cannot read, malformed JSON in the HTML.
"""
import json, os, random, re, sys, tempfile

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import api  # noqa: E402

random.seed(3)
TEAMS = [{"id": i, "short_name": f"T{i:02d}", "code": 100 + i} for i in range(1, 21)]
EV = [4, 5, 6]
SHAPE = {1: 2, 2: 5, 3: 5, 4: 3}

elements, eid = [], 1
owners = {}
def add(pos, owner, mins=270, starts=3, q=4.0):
    global eid
    elements.append({"id": eid, "code": 9000 + eid, "web_name": f"Player{eid}",
        "team": (eid % 20) + 1, "element_type": pos, "minutes": mins, "starts": starts,
        "points_per_game": q, "form": q, "expected_goal_involvements": q / 8,
        "expected_goals": q / 10, "expected_goals_conceded": 1.2,
        "status": "a", "chance_of_playing_next_round": None, "news": ""})
    owners[eid] = owner; eid += 1
for pos, n in SHAPE.items():
    for _ in range(n): add(pos, 4242)
for entry in range(1, 9):
    for pos, n in SHAPE.items():
        for _ in range(n): add(pos, entry)
for _ in range(120):
    add(random.choice([1,2,3,4]), None, q=random.uniform(1, 6))

BOOT = {"elements": elements, "teams": TEAMS,
        "events": {"data": [{"id": e, "deadline_time": f"2026-09-{10+e}T12:30:00Z"} for e in EV]},
        "fixtures": {str(e): [{"team_h": t, "team_a": t + 1} for t in range(1, 20, 2)] for e in EV}}

api.bootstrap = lambda: BOOT
api.game = lambda: {"current_event": 3, "current_event_finished": True, "next_event": 4}
api.league_details = lambda lid: {"league": {"name": "Test League"},
    "league_entries": [{"entry_id": 4242, "entry_name": "My Side",
                        "player_first_name": "A", "player_last_name": "B"}]}
api.element_status = lambda lid: {"element_status": [{"element": k, "owner": v} for k, v in owners.items()]}

import build  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    cfg = os.path.join(tmp, "config.yml")
    with open(os.path.join(HERE, "..", "config.yml")) as fh:
        src = fh.read()
    src = src.replace("league_id: 27214", "league_id: 1").replace("entry_id: 141674", "entry_id: 4242")
    open(cfg, "w").write(src)

    sys.argv = ["build", "--config", cfg,
                "--template", os.path.join(HERE, "..", "template", "waiver-wire.html"),
                "--out-dir", tmp]
    rc = build.main()
    assert rc == 0, f"build returned {rc}"

    html = open(os.path.join(tmp, "index.html")).read()
    assert "__SEED__" not in html, "marker was not substituted"
    seed = json.loads(re.search(r'id="seedData">(.*?)</script>', html, re.S).group(1))
    disk = json.load(open(os.path.join(tmp, "waivers.json")))
    assert seed == disk, "embedded seed and published json disagree"

    for key in ("generated","EV","baseline","baseGW","claims","squad","pool","teamCodes","deadline"):
        assert key in seed, f"payload missing {key}"
    assert len(seed["squad"]) == 15
    assert len(seed["teamCodes"]) == 20
    for p in seed["squad"]:
        assert len(p["proj"]) == len(seed["EV"]) and len(p["fx"]) == len(seed["EV"])
        assert abs(sum(p["proj"]) - p["h"]) < 0.06
    for c in seed["claims"]:
        assert set(c["per"]) == {str(e) for e in seed["EV"]}
        assert abs(sum(c["per"].values()) - c["gain"]) < 0.05
        assert c["drop"]["n"] in {p["n"] for p in seed["squad"]}
    # the page's own optimiser must reproduce the stated baseline
    F = [(d,m,f) for d in range(3,6) for m in range(2,6) for f in range(1,4) if d+m+f==10]
    for i, ev in enumerate(seed["EV"]):
        bp = {1:[],2:[],3:[],4:[]}
        for p in seed["squad"]: bp[p["pos"]].append(p)
        for k in bp: bp[k].sort(key=lambda p: -p["proj"][i])
        best = max(sum(p["proj"][i] for p in bp[1][:1]+bp[2][:d]+bp[3][:m]+bp[4][:f])
                   for d,m,f in F if len(bp[2])>=d and len(bp[3])>=m and len(bp[4])>=f)
        assert abs(best - seed["baseGW"][str(ev)]) < 0.08, (ev, best, seed["baseGW"][str(ev)])
    print("\nbuild plumbing OK: template injected, payload valid, baselines reproduce")
