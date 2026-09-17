# DTF Club — Career Head-to-Head Record

A self-updating career head-to-head win/loss matrix for the league, pulled
from Sleeper's public API, rendered as a static page, and hosted for free on
GitHub Pages so it can be embedded live in the league's Google Site.

## How it works

- `history_baseline.tsv` is the frozen **regular season** historical record
  (2021 through 2026 week 1), exactly as compiled from the league. **This
  file is never rewritten automatically** — it's the permanent source of
  truth for everything already played, so past totals never silently change.
- `playoff_baseline.tsv` is the frozen **playoff** head-to-head record for
  2021–2025 — every postseason matchup from back when the league was 10
  teams, including the three years with a 3-team championship (highest
  score wins; the other two each take a loss, so those years contribute two
  results instead of one game). That format is retired now that the league
  is 12 teams — **2026 onward, playoff results are pulled automatically**
  from Sleeper's own bracket data, the same as the regular season.
- `build_matrix.py` runs on a schedule (once a day at 9:00 UTC, via GitHub
  Actions — see `.github/workflows/update.yml` to change the time).
  Each run:
  1. Auto-discovers the current season's Sleeper league (no league ID to
     update, ever — it looks up Ben's leagues for the current NFL season).
  2. Walks the league chain back through `previous_league_id` to confirm the
     season history.
  3. Pulls any new completed regular-season weeks from Sleeper that aren't
     already in the baseline, using the same "everyone plays a normal game"
     rule the historical data follows (a week only counts once every team
     has played a standard 1-vs-1 matchup — once the bracket splits into
     uneven playoff/consolation games, it stops counting for that season).
     That cutoff is also capped by whatever `playoff_week_start` the league
     itself reports for that season (week 15 for 2021–2023/2025, week 14
     starting in 2026 per the new regular-season length).
  4. For any season after 2025 that Sleeper marks `complete`, pulls that
     season's playoff bracket (`winners_bracket`) and adds every decided
     game to the playoff table — no hand-entry needed anymore, since a
     normal 12-team bracket has no 3-team-final wrinkle to work around. It
     deliberately waits until the season is marked complete before counting
     any playoff game, so an in-progress round is never shown as final.
  5. Recomputes both matrices and writes `index.html`, which has two tables:
     regular season on top, playoffs below.
  6. Commits the updated page back to the repo if anything changed.
- GitHub Pages serves `index.html` at your Pages URL, which is what you
  embed in Google Sites.

### If the playoff format ever changes again

If a future season goes back to some non-standard format (byes, a
multi-team finale, etc.) that Sleeper's bracket API can't represent
cleanly, add that season to `FROZEN_PLAYOFF_SEASONS` near the top of
`build_matrix.py` and hand-enter its games in `playoff_baseline.tsv`
(one `Winner<TAB>Loser` line per result) the same way 2021–2025 work.

No API key or login is needed anywhere — Sleeper's API is public read-only,
and GitHub Actions' built-in token is what commits the update.

## One-time setup (about 5 minutes)

1. **Create a new repository on GitHub** (Settings can be Public — nothing
   sensitive lives here, it's just win/loss records). Name it whatever you
   like, e.g. `dtf-club-h2h`.
2. **Upload these files** to the repo: on the repo's main page, click
   *Add file → Upload files*, and drag in this whole folder (keep the
   `.github/workflows/update.yml` path intact — GitHub will preserve it).
   Commit directly to `main`.
3. **Turn on GitHub Pages**: repo *Settings → Pages* → under "Build and
   deployment", set Source to **Deploy from a branch**, Branch to **main**
   and folder to **/ (root)**. Save.
4. **Run the workflow once manually** so it doesn't wait until the next day: go to the
   *Actions* tab → "Update head-to-head record" → *Run workflow*. It'll pull
   live data, confirm the baseline still matches, and commit `index.html`.
5. **Grab the Pages URL**: still in *Settings → Pages*, GitHub will show
   something like `https://<your-username>.github.io/<repo-name>/`. Open it
   to confirm the table shows up.
6. **Embed it in Google Sites**: edit your Site → *Insert → Embed → By URL*
   → paste the Pages URL → Insert. Resize the embed box as needed.

That's it — from here it updates itself. Nothing else to touch during the
season. Each new season, once the commissioner rolls the league over on
Sleeper (which sets `previous_league_id` automatically), the script picks it
up on its own — no config change needed.

## If someone new joins or leaves the league

Open `build_matrix.py` and add their Sleeper `user_id` → name to the
`MANAGER_MAP` dictionary near the top (their `user_id` can be read from
`https://api.sleeper.app/v1/league/<current_league_id>/users`). If you skip
this, they'll simply show up under their Sleeper display name instead of a
real name, per how this was set up.

## Running it locally (optional, for testing)

```
python3 build_matrix.py
```

No dependencies beyond Python 3's standard library.
