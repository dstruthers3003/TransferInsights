"""Projection, best eleven, and claim ranking.

This is the same engine as the one embedded in the page, kept in step by
hand. If you change a weight here, change it there too - the smoke test
checks that the two agree on the numbers it can see.

The shape of it:

    projection = rate x start probability x fixture

summed over however many fixtures a team has in a gameweek, so a blank scores
nothing and a double scores twice with no special handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

POSSHORT = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
SQUAD_SHAPE = {1: 2, 2: 5, 3: 5, 4: 3}

# (DEF, MID, FWD). One keeper is mandatory, ten outfield.
FORMATIONS = [
    (d, m, f)
    for d, m, f in product(range(3, 6), range(2, 6), range(1, 4))
    if d + m + f == 10
]


@dataclass
class Player:
    element: int
    code: int
    name: str
    team: int
    team_short: str
    pos: int
    owner: int | None
    ppg: float
    form: float
    minutes: int
    starts: int
    xgi90: float
    status: str
    chance: int | None
    news: str
    recent_starts: list[int] = field(default_factory=list)   # oldest to newest
    recent_minutes: list[int] = field(default_factory=list)  # oldest to newest
    start_prob: float = 0.0
    rate: float = 0.0
    proj: list[float] = field(default_factory=list)
    fixtures: list[list[dict]] = field(default_factory=list)

    @property
    def horizon(self) -> float:
        return round(sum(self.proj), 1)


# --- team strength ----------------------------------------------------------


def team_strength(elements: list[dict], teams: list[dict], played: int) -> dict:
    """Attack and defence per team, from their own underlying numbers.

    The Draft API publishes no difficulty ratings and no team strength, so
    both are derived. Attack is the squad's total expected goals, which is
    additive and behaves. Defence is the first-choice keeper's expected goals
    conceded - summing that across a squad would multiply it by however many
    players were on the pitch, so the keeper's own figure is the cleanest
    team-level number available.

    This has one advantage over the published ratings in the main game: those
    are set before a ball is kicked and never move. These update every week.
    """
    played = max(played, 1)
    agg = {t["id"]: {"xg": 0.0, "xgc": 0.0} for t in teams}
    for e in elements:
        bucket = agg.get(e["team"])
        if bucket is None:
            continue
        bucket["xg"] += float(e.get("expected_goals") or 0)
        if e["element_type"] == 1:
            bucket["xgc"] = max(bucket["xgc"], float(e.get("expected_goals_conceded") or 0))
    return {tid: {"atk": v["xg"] / played, "def": v["xgc"] / played} for tid, v in agg.items()}


def fixture_multiplier(strength: dict, opp_id: int, pos: int, cfg: dict, confidence: float) -> float:
    """Scale for the opposition.

    Keepers and defenders are measured against the opponent's attack;
    midfielders and forwards against the opponent's defence. Same opponent,
    opposite sign. Defenders swing hardest because clean sheets are close to
    binary on opposition quality, while attacking returns are not.

    Early in a season the whole adjustment is shrunk toward neutral, since a
    handful of matches is not much evidence.
    """
    opp = strength.get(opp_id)
    if not opp:
        return 1.0

    mean_atk = cfg["_mean_atk"]
    mean_def = cfg["_mean_def"]
    swing = cfg["fdr_swing"][POSSHORT[pos]]

    if pos <= 2:
        rel = (opp["atk"] - mean_atk) / mean_atk if mean_atk else 0.0
        raw = 1 - rel * swing
    else:
        rel = (opp["def"] - mean_def) / mean_def if mean_def else 0.0
        raw = 1 + rel * swing

    clamped = max(cfg["fdr_floor"], min(cfg["fdr_ceiling"], raw))
    return 1 + (clamped - 1) * confidence


# --- per-player numbers -----------------------------------------------------


def _weights(n: int, decay: float) -> list[float]:
    """Oldest to newest, each older gameweek worth `decay` times the next."""
    return [decay ** (n - 1 - i) for i in range(n)]


def _decay_for(pos: int, cfg: dict) -> float:
    """How fast old gameweeks stop mattering, by position.

    Goalkeepers get a much sharper decay than outfielders, because the
    position works differently. A keeper is either the first choice or he is
    not; managers hand over the gloves and then leave them alone, so a keeper
    who has started the last two games is the number one and what happened
    before the change tells you nothing. Outfielders are genuinely rotated, so
    their history deserves more weight.
    """
    per = cfg.get("recency_decay_by_position") or {}
    return per.get(POSSHORT[pos], cfg["recency_decay"])


def start_probability(p: Player, played: int, cfg: dict) -> float:
    """How likely this player is to be on the pitch, in [0, 1].

    Hard flags come first because they are real information. Otherwise the
    estimate comes from per-gameweek history, weighted so that recent weeks
    count for far more than old ones.

    Two things drove this design.

    Starts are the signal, not minutes. A striker who starts every week and is
    replaced on the hour plays about 55 minutes a game; scoring him on minutes
    share would read 60% and badly undervalue a nailed-on starter. Whether he
    was in the eleven is the thing that matters.

    But minutes still matter as a floor, because a regular substitute is worth
    more than nothing. So the estimate is the higher of weighted starts and
    weighted minutes share.

    Weighting the tail is what separates "not yet" from "not any more". A
    keeper signed last month who was an unused substitute for his first
    gameweek and has started every one since is not a 67% starter, but a
    season average cannot tell that apart from a player who has just been
    dropped. Those are opposite signals with identical averages.

    Gameweeks a player was not registered for are absent from the history
    rather than recorded as zero, so a mid-season signing is judged on the
    games he could actually have played.

    Falls back to season totals when no per-gameweek history is available,
    taking the higher of start share and minutes share so that a fit player
    yet to start is never valued at exactly zero.
    """
    if p.status in ("i", "s", "u"):
        return 0.0
    if p.chance is not None:
        return p.chance / 100.0

    if p.recent_starts:
        w = _weights(len(p.recent_starts), _decay_for(p.pos, cfg))
        total = sum(w)
        by_starts = sum(x * min(s, 1) for x, s in zip(w, p.recent_starts)) / total
        mins = p.recent_minutes or [0] * len(p.recent_starts)
        by_minutes = sum(x * min(m / 90.0, 1.0) for x, m in zip(w, mins)) / total
        rate = max(by_starts, by_minutes)
    else:
        played = max(played, 1)
        rate = max(p.starts / played, p.minutes / (90.0 * played))

    rate = min(rate, 1.0)
    if p.status == "d":
        rate *= cfg["doubtful_penalty"]
    return round(rate, 2)


def scoring_rate(p: Player, cfg: dict) -> float:
    """Expected points per appearance, with thin samples distrusted.

    Season points-per-game is stable but slow, form is fast but noisy, and
    expected goal involvements lead actual returns. The blend is then pulled
    toward a baseline for the position in proportion to how few starts the
    player has - two starts and a brace produces a gaudy average built on
    nothing. The pull is negligible by about ten starts.
    """
    w = cfg["weights"]
    blended = (
        w["season"] * p.ppg
        + w["form"] * p.form
        + w["underlying"] * (p.xgi90 * cfg["xgi_to_points"][POSSHORT[p.pos]] + cfg["appearance_points"])
    )
    k = cfg["shrinkage_starts"]
    confidence = p.starts / (p.starts + k) if (p.starts + k) else 0.0
    baseline = cfg["position_baseline"][POSSHORT[p.pos]]
    return round(max(confidence * blended + (1 - confidence) * baseline, 0.0), 2)


def project(players: list[Player], strength: dict, opponents: dict, events: list[int],
            played: int, cfg: dict) -> None:
    """Fill in start_prob, rate, proj and fixtures on every player."""
    means = [s for s in strength.values()]
    cfg["_mean_atk"] = sum(s["atk"] for s in means) / len(means) if means else 0.0
    cfg["_mean_def"] = sum(s["def"] for s in means) / len(means) if means else 0.0
    confidence = min(played / cfg["fdr_confidence_gws"], 1.0)

    for p in players:
        p.start_prob = start_probability(p, played, cfg)
        p.rate = scoring_rate(p, cfg)
        p.proj = []
        p.fixtures = []
        for ev in events:
            games = opponents.get(p.team, {}).get(ev, [])
            p.fixtures.append([{"o": g["short"], "h": g["home"]} for g in games])
            total = sum(
                p.rate * p.start_prob * fixture_multiplier(strength, g["opp"], p.pos, cfg, confidence)
                for g in games
            )
            p.proj.append(round(total, 2))


def build_opponents(fixtures: dict, events: list[int], short_of: dict) -> dict:
    """team -> gameweek -> list of opponents, with home/away."""
    out: dict[int, dict[int, list[dict]]] = {}
    for ev in events:
        for f in fixtures.get(str(ev), fixtures.get(ev, [])) or []:
            h, a = f["team_h"], f["team_a"]
            out.setdefault(h, {}).setdefault(ev, []).append({"opp": a, "short": short_of.get(a, "?"), "home": 1})
            out.setdefault(a, {}).setdefault(ev, []).append({"opp": h, "short": short_of.get(h, "?"), "home": 0})
    return out


# --- the eleven -------------------------------------------------------------


def best_eleven(squad: list[Player], ix: int) -> tuple[float, list[Player]]:
    """Highest-scoring legal side for one gameweek."""
    by_pos: dict[int, list[Player]] = {1: [], 2: [], 3: [], 4: []}
    for p in squad:
        by_pos[p.pos].append(p)
    for group in by_pos.values():
        group.sort(key=lambda p: p.proj[ix] if ix < len(p.proj) else 0, reverse=True)

    if not by_pos[1]:
        return 0.0, []

    best_total, best_team = -1.0, []
    for d, m, f in FORMATIONS:
        if len(by_pos[2]) < d or len(by_pos[3]) < m or len(by_pos[4]) < f:
            continue
        team = by_pos[1][:1] + by_pos[2][:d] + by_pos[3][:m] + by_pos[4][:f]
        total = sum(p.proj[ix] for p in team)
        if total > best_total:
            best_total, best_team = total, team
    return max(best_total, 0.0), best_team


def pick_eleven(squad: list[Player], ix: int) -> dict:
    """Best eleven for one gameweek, with the shape and the players named.

    best_eleven returns only the total because that is all the claim maths
    needs. This returns the side itself, for showing an opponent's team.
    """
    total, xi = best_eleven(squad, ix)
    if not xi:
        return {"total": 0.0, "shape": "-", "xi": [], "bench": []}
    counts = {2: 0, 3: 0, 4: 0}
    for p in xi:
        if p.pos in counts:
            counts[p.pos] += 1
    chosen = {p.element for p in xi}
    bench = [p for p in squad if p.element not in chosen]
    # the spare keeper first: he can only ever replace the keeper
    bench.sort(key=lambda p: (p.pos != 1, -(p.proj[ix] if ix < len(p.proj) else 0)))
    return {
        "total": round(total, 1),
        "shape": f"{counts[2]}-{counts[3]}-{counts[4]}",
        "xi": xi,
        "bench": bench,
    }


# --- claims -----------------------------------------------------------------


@dataclass
class Claim:
    add: Player
    drop: Player
    gain: float
    per_gw: dict[int, float]


def rank_claims(squad: list[Player], free: list[Player], events: list[int], cfg: dict) -> list[Claim]:
    """Every worthwhile add and drop, best first.

    A free agent is worth claiming only insofar as he changes the eleven you
    actually field. A brilliant midfielder is worth nothing if your worst
    starting midfielder is already better. So each candidate is valued as the
    difference between your best possible eleven now and with the swap made.

    Draft enforces exact squad composition, so claims are always like-for-like
    by position, which is what makes this cheap enough to brute force.
    """
    baseline = {i: best_eleven(squad, i)[0] for i in range(len(events))}

    raw: list[Claim] = []
    for candidate in free:
        if candidate.start_prob <= 0 or candidate.minutes < cfg["min_minutes"]:
            continue
        best: Claim | None = None
        for outgoing in [p for p in squad if p.pos == candidate.pos]:
            trial = [p for p in squad if p.element != outgoing.element] + [candidate]
            per = {}
            gain = 0.0
            for i, ev in enumerate(events):
                delta = best_eleven(trial, i)[0] - baseline[i]
                per[ev] = round(delta, 2)
                gain += delta
            if best is None or gain > best.gain:
                best = Claim(candidate, outgoing, round(gain, 2), per)
        if best and best.gain >= cfg["min_gain"]:
            raw.append(best)

    raw.sort(key=lambda c: c.gain, reverse=True)

    # Claims sharing a drop are alternatives, not additions - you only have
    # the one player to give up. Keep a fallback for each and cut the rest,
    # or the list fills with eight ways to replace your worst defender.
    kept: list[Claim] = []
    used: dict[int, int] = {}
    for c in raw:
        seen = used.get(c.drop.element, 0)
        if seen >= cfg["max_per_drop"]:
            continue
        used[c.drop.element] = seen + 1
        kept.append(c)
        if len(kept) >= cfg["max_claims"]:
            break
    return kept
