# DTF Club: Career Head-to-Head Record & Rumbles Live Standings

Two self-updating pages for the league, both pulled from Sleeper's public
API, rendered as static pages, and hosted for free on GitHub Pages so they
can be embedded live in the league's Google Site:

- `index.html`: the career head-to-head win/loss matrix (see below).
- `rumbles.html`: the season's live Rumbles standings, including a truly
  live, real-time view of whichever week is currently being played (see
  "Rumbles live standings" further down).

## How it works

- `history_baseline.tsv` is the frozen **regular season** historical record
  (2021 through 2026 week 1), exactly as compiled from the league. **This
  file is never rewritten automatically**, so it's the permanent source of
  truth for everything already played and past totals never silently change.
  Hovering any cell in the regular-season table shows that pair's full
  chronological matchup history, including the actual score of each game
  where it's still available, and if one manager has won 2 or more of
  their last games in a row, that streak is called out in green at the
  bottom of the tooltip. This is regular-season only; the playoff table
  doesn't have reliable week numbers for the hand-entered years, so it's
  left without tooltips.
  - Scores are re-fetched live from Sleeper every run for every regular
    season week already in the baseline, not just new ones, purely to
    annotate the tooltip (win/loss totals always still come only from the
    frozen baseline). If a manager ever leaves the league and Sleeper stops
    reporting an owner for their old roster at all (`owner_id` goes back to
    null instead of being reassigned), there's no live API call left that
    can recover who played it, so that roster needs a manual entry in
    `ROSTER_OVERRIDES` near the top of `build_matrix.py` (keyed by that
    season's `league_id` and `roster_id`) to keep showing scores for their
    games. One case like this exists today, already handled: Tommy's
    2023 roster.
- `playoff_baseline.tsv` is the frozen **playoff** head-to-head record, with
  columns `Season`, `Winner`, `Loser`. It covers 2010 through 2025, every
  known postseason matchup from before this was tracked on Sleeper,
  including the three seasons that ended in a 3-team championship (highest
  score wins, and the other two each take a loss, so those seasons
  contribute two results instead of one game). That format is retired now
  that the league is 12 teams, so **2026 onward, playoff results are pulled
  automatically** from Sleeper's own bracket data, the same as the regular
  season.
  - 2010 and 2011 are incomplete: only the finals matchup is fully known
    (Jake over Zak in 2010, Ryan over Alex in 2011). Whoever each finalist
    beat in the semifinal to get there isn't recorded, so that extra win is
    credited to the finalist's career total with no opponent attached
    (`PLAYOFF_IMPLIED_WINS` near the top of `build_matrix.py`) and shown
    with an asterisk on the playoff table instead of being guessed at.
- `build_matrix.py` runs on a schedule (once a day at 9:00 UTC, via GitHub
  Actions; see `.github/workflows/update.yml` to change the time).
  Each run:
  1. Auto-discovers the current season's Sleeper league (no league ID to
     update, ever; it looks up Ben's leagues for the current NFL season).
  2. Walks the league chain back through `previous_league_id` to confirm the
     season history, and reads the league's current name straight off
     Sleeper for the page title and headings, so renaming the league on
     Sleeper is all it takes to rename this page too.
  3. Pulls any new completed regular-season weeks from Sleeper that aren't
     already in the baseline, using the same "everyone plays a normal game"
     rule the historical data follows (a week only counts once every team
     has played a standard 1-vs-1 matchup; once the bracket splits into
     uneven playoff/consolation games, it stops counting for that season).
     That cutoff is also capped by whatever `playoff_week_start` the league
     itself reports for that season (week 15 for 2021 through 2023 and
     2025, week 14 starting in 2026 per the new regular-season length).
  4. For any season after the ones listed in `FROZEN_PLAYOFF_SEASONS` that
     Sleeper marks `complete`, pulls that season's playoff bracket
     (`winners_bracket`) and adds every decided game to the playoff table,
     no hand-entry needed anymore, since a normal 12-team bracket has no
     3-team-final wrinkle to work around. It deliberately waits until the
     season is marked complete before counting any playoff game, so an
     in-progress round is never shown as final.
  5. Recomputes both matrices and writes `index.html`, which has two
     tables: regular season on top, playoffs below. The season ranges and
     game counts in each table's description are computed from the data
     itself, so they keep up on their own as new seasons are added.
  6. Commits the updated page back to the repo if anything changed.
- GitHub Pages serves `index.html` at your Pages URL, which is what you
  embed in Google Sites.

### If the playoff format ever changes again

If a future season goes back to some non-standard format (byes, a
multi-team finale, and so on) that Sleeper's bracket API can't represent
cleanly, add that season to `FROZEN_PLAYOFF_SEASONS` near the top of
`build_matrix.py` and hand-enter its games in `playoff_baseline.tsv` (one
`Season<TAB>Winner<TAB>Loser` line per result) the same way 2010 through
2025 work.

No API key or login is needed anywhere. Sleeper's API is public read-only,
and GitHub Actions' built-in token is what commits the update.

## Rumbles live standings (`rumbles.html`)

"Rumbles" is the league's own custom weekly scoring system, on top of normal
head-to-head wins and losses: winning your head-to-head matchup is worth 9
Rumbles, plus 1 more Rumble for every other team in the league you outscore
that week (a 12-team round robin, so up to 20 Rumbles in a single week: 9 for
the win plus 11 for having the top score). They're cumulative over the
season. This page shows the running standings built entirely from that
formula, live.

- `build_rumbles.py` runs alongside `build_matrix.py` on the same daily
  schedule. For every week of the **current season** that's fully finished,
  it pulls the actual final scores from Sleeper, computes each manager's
  Rumbles for that week, and writes the running totals (Rumbles, Rumble %,
  points for/against, head-to-head record, and record against the field) to
  `rumbles_history.json`. It's season-scoped on purpose. Rumbles resets
  every year, unlike the career head-to-head matrix.
- `rumbles.html` loads that file for everything already finished, then adds
  the **current, in-progress week live**, computed right in your browser: it
  polls Sleeper directly every 30 seconds (and on demand with the "Refresh
  now" button) for live scores, so during gameday the standings update in
  real time as players actually play, without waiting for the next daily
  build.
  - For any starter who hasn't played yet, it fills in a projection instead
    of an actual score. There's a toggle for which projection to use:
    **Generic Sleeper PPR** (Sleeper's own generic projection, the default)
    or **Our Custom Scoring** (the same raw per-player projected stats, but
    weighted by the league's actual scoring settings, pulled live from
    Sleeper too, so it reflects things generic PPR doesn't, like this
    league's first-down bonuses). The toggle never touches anything that's
    already been played. It only decides how the not-yet-played portion of
    the live week is estimated.
  - If Sleeper's API is briefly unreachable from someone's browser, the page
    just shows the last successfully loaded data and says so, rather than
    breaking.
- For now, keep updating the "2026 TRUE STANDINGS" Google Sheet by hand as
  usual; this page is a live, gameday-only view alongside it. Down the road,
  once it's been trusted for a while, it's meant to fully replace the manual
  sheet.

## One-time setup (about 5 minutes)

1. **Create a new repository on GitHub** (Settings can be Public, since
   nothing sensitive lives here, it's just win/loss records). Name it
   whatever you like, e.g. `dtf-club-h2h`.
2. **Upload these files** to the repo: on the repo's main page, click
   *Add file → Upload files*, and drag in this whole folder (keep the
   `.github/workflows/update.yml` path intact; GitHub will preserve it).
   Commit directly to `main`.
3. **Turn on GitHub Pages**: repo *Settings → Pages* → under "Build and
   deployment", set Source to **Deploy from a branch**, Branch to **main**
   and folder to **/ (root)**. Save.
4. **Run the workflow once manually** so it doesn't wait until the next
   day: go to the *Actions* tab → "Update head-to-head record" → *Run
   workflow*. It'll pull live data, confirm the baseline still matches, and
   commit `index.html` and `rumbles_history.json`.
5. **Grab the Pages URLs**: still in *Settings → Pages*, GitHub will show
   something like `https://<your-username>.github.io/<repo-name>/`. Open it
   to confirm the head-to-head table shows up, and open
   `https://<your-username>.github.io/<repo-name>/rumbles.html` to confirm
   the Rumbles standings show up too.
6. **Embed both in Google Sites**: edit your Site → *Insert → Embed → By
   URL* → paste the Pages URL → Insert. Do this once for the head-to-head
   page and again (as a separate embed) for the `rumbles.html` URL. Resize
   each embed box as needed.

That's it, from here it updates itself. Nothing else to touch during the
season. Each new season, once the commissioner rolls the league over on
Sleeper (which sets `previous_league_id` automatically), the script picks it
up on its own with no config change needed.

## If someone new joins or leaves the league

Open `build_matrix.py` and add their Sleeper `user_id` to name mapping to
the `MANAGER_MAP` dictionary near the top (their `user_id` can be read from
`https://api.sleeper.app/v1/league/<current_league_id>/users`). If you skip
this, they'll simply show up under their Sleeper display name instead of a
real name, per how this was set up. `build_rumbles.py` reuses the same
`MANAGER_MAP`, so there's only ever one place to update.

If someone who's already left shows up with `"owner_id": null` on their old
roster when you check `.../rosters` (rather than being reassigned to
whoever took their spot), that roster can no longer be resolved from live
data at all, past or present. Add it to `ROSTER_OVERRIDES`, a few lines
below `MANAGER_MAP`, as `"<that season's league_id>": {<roster_id>: "Name"}`
so their historical games keep showing up correctly (this only affects the
hover-tooltip scores; win/loss totals always come from the frozen baseline
regardless).

## Running it locally (optional, for testing)

```
python3 build_matrix.py
python3 build_rumbles.py
```

No dependencies beyond Python 3's standard library. `rumbles.html` itself
has no build step; it's plain HTML/JS that runs entirely in the browser, so
just opening it (served over http/https, not as a bare `file://` page,
since it needs to fetch `rumbles_history.json`) is enough to test it.
