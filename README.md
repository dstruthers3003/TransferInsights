# fpl-waiver-wire

Ranked waiver claims and a best eleven for **Paisley & District Draft**,
rebuilt every morning by GitHub Actions and published as a page you open on
your phone.

League `27214`, entry `141674`. Change both in `config.yml`.

## What it does, and what it will not do

Every morning it reads the public Draft API, projects every player over the
gameweeks the API has fixtures for, works out which free agents would actually
improve your starting eleven, and publishes the result.

It does not submit anything. FPL Draft has no API token, so automating
submission would mean storing your Premier League password as a repository
secret and replaying it on every run. That is against their terms, it breaks
the moment they add two-factor, and a leaked Actions secret would be your live
account. The analysis is automated; entering three claims takes you about
ninety seconds.

**No credentials are used anywhere in this repo, and no secrets need setting
up.** Every endpoint it touches was verified to answer with cookies
suppressed, which is the same position the CI runner is in.

## Setup

1. Push this directory to a new repository.
2. Settings → Pages → Source: **GitHub Actions**.
3. Settings → Actions → General → Workflow permissions: **Read and write**.
4. Actions → *Update waivers* → **Run workflow**, rather than waiting for
   tomorrow morning.

Your page appears at `https://<you>.github.io/<repo>/`. Add it to your home
screen; the link never changes.

Locally:

```bash
pip install -r requirements.txt
python src/build.py            # writes docs/
open docs/index.html
```

## How a claim is valued

The obvious approach is to rank free agents by projected points. That is wrong
for draft. A midfielder projected at six points a game is worth nothing to you
if your worst starting midfielder is already on six and a half.

So each candidate is valued by what he does to your **best possible eleven**.
For each gameweek the optimiser picks the highest-scoring legal side from your
fifteen, once as things stand and once with the swap made. The difference is
the claim's value. Players who would sit on your bench come out at zero and
drop off the list on their own.

Draft enforces exact squad composition, so claims are always like-for-like by
position, which is what makes this cheap enough to brute force over every
candidate against every possible drop.

### The projection

```
projection = rate x start probability x fixture
```

summed over however many fixtures a team has that gameweek, so a blank scores
nothing and a double scores twice with no special handling.

**Rate** blends season points-per-game, recent form and expected goal
involvements per 90, then shrinks the result toward a positional baseline in
proportion to how few starts the player has. Two starts and a brace produces a
gaudy average built on nothing; without shrinkage those players dominate the
list. The pull is negligible by about ten starts.

**Start probability** takes the availability flag and percentage where the
game gives one, otherwise the higher of start share and minutes share. Anyone
flagged injured or suspended is zero and never appears as a claim.

**Fixture** is derived, because the Draft API publishes no difficulty ratings
and no team strength. A team's attack is its squad's total expected goals; its
defence is its first-choice keeper's expected goals conceded, which is the
cleanest team-level figure available. Keepers and defenders are scaled against
the opponent's attack, midfielders and forwards against the opponent's
defence. This beats the published ratings in the main game on one count: those
are fixed before the season and never move, while these update weekly. Early
on the whole adjustment is shrunk toward neutral.

## Known limitations

Read these before trusting it.

- **Team news is invisible.** Minutes only ever *confirm* a change after it
  shows up. A signing arriving in your player's position, or a manager naming
  him third choice, is reported the day it happens and will not reach these
  numbers for two or three gameweeks. Check the news on anyone near the top of
  the list before claiming. This is the single biggest gap and it is not
  closeable from the API.
- **The horizon is about three gameweeks**, because that is all the API
  publishes fixtures for. A fixture swing four weeks out is invisible.
- **Waiver order is not modelled.** Claims are ranked by value to you. Nothing
  here knows where you sit in the queue or what your rivals want.
- **No opponent modelling.** In head-to-head the right move is sometimes
  blocking a rival rather than maximising your own projection.
- **Set pieces and penalties are not tracked**, one of the larger things the
  underlying numbers miss.
- **Upside is not valued.** A benched player at a strong side may be worth
  more in March than a guaranteed starter at a poor one, and you cannot get
  him back once dropped. This values three gameweeks and nothing else.
- **Scoring is assumed to match the main game.** If your league scores
  anything differently, every number is biased by the same factor. Rankings
  survive that; point totals do not.

## Why it runs daily rather than weekly

A gameweek can finish on a Monday night. A snapshot taken while one is still
being played is built on partial minutes and quietly skews every projection —
that happened during development and produced a squad rated four points below
its real strength, with the wrong formation. `current_event` still reads the
old number while matches are in flight, so the data looks settled when it is
not. The published payload carries `gwFinished` for exactly this reason, and
running daily means a bad snapshot is replaced within a day.

## Layout

```
src/api.py        read-only Draft API client
src/engine.py     projection, best eleven, claim ranking
src/build.py      fetch, compute, write docs/
template/         the page, with a __SEED__ marker the build fills in
docs/             generated: index.html and waivers.json
tests/            offline tests, run in CI before every publish
```

The page prefers `waivers.json` when it can reach it, falling back to the copy
baked into the HTML. That is what lets a saved copy work offline on a phone,
and lets a tab left open pick up a new run.

The engine exists twice: once in Python here, once in JavaScript inside the
page so it can recompute the eleven when you change gameweek, and once more in
the console script on the Data tab for manual refreshes. They are kept in step
by hand. `tests/build_offline.py` checks that the page's optimiser reproduces
the baseline the Python one published, which catches the two drifting apart.

## Tuning

Everything is in `config.yml`, commented. Two worth touching first:

- **`weights`** — if recommendations feel like they are chasing last week's
  points, shift weight from `form` toward `underlying`.
- **`shrinkage_starts`** — raise it to be more sceptical of hot starts.

After a few weeks, compare `docs/waivers.json` against what those players
actually scored. If projections run consistently high or low, the fix is
usually `appearance_points` or the `xgi_to_points` rates.
