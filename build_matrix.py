#!/usr/bin/env python3
"""
DTF Club - career head-to-head record builder.

What this does, every time it runs:
  1. Loads the frozen regular-season baseline (history_baseline.tsv) exactly
     as compiled by the league -- this file is never rewritten automatically,
     so past seasons' numbers never silently change.
  2. Auto-discovers this season's Sleeper league (no league ID to maintain --
     it looks up the anchor manager's leagues for the current NFL season) and
     walks previous_league_id backward to confirm the season chain. The page
     title and headings use the league's actual current name on Sleeper, so
     renaming the league on Sleeper is all it takes to rename this page too.
  3. For any season/week NOT already in the baseline (this season's games
     after the baseline's last recorded week, and any future season), it
     pulls real matchup results from Sleeper's public API and appends them,
     but ONLY for weeks where every team in the league played a normal 1-vs-1
     game that week (the "everyone plays" rule -- once a week has a bye or an
     uneven bracket, it's a playoff/consolation split and stops counting).
     That cutoff is also capped by the league's own `playoff_week_start`
     setting when Sleeper reports one (e.g. week 15 in 2021-2023/2025, week 14
     starting 2026), so a season with a full round-robin schedule but no
     playoff bracket configured (like 2024, where Sleeper reports 0) can't
     over-count.
  4. Loads the frozen playoff baseline (playoff_baseline.tsv) -- hand-entered
     career playoff results through the last season listed in
     FROZEN_PLAYOFF_SEASONS, including the years with a 3-team championship
     (highest score wins, the other two each take a loss). Every season after
     that is pulled automatically from Sleeper's own bracket data once the
     season is marked complete, the same way the regular season is.
  5. Recomputes both career win-loss matrices and renders index.html with
     both tables.

No API key needed (Sleeper's API is public read-only). No state is kept
between runs other than git history -- every run recomputes everything from
scratch, so it's self-healing.
"""

import csv
import html
import json
import sys
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

API_BASE = "https://api.sleeper.app/v1"
HERE = Path(__file__).resolve().parent
BASELINE_PATH = HERE / "history_baseline.tsv"
PLAYOFF_BASELINE_PATH = HERE / "playoff_baseline.tsv"
OUTPUT_HTML = HERE / "index.html"

# The manager whose account we use to auto-discover each new season's league.
# ("namkurd" = Ben; reused every year since he's always in the league.)
ANCHOR_USER_ID = "611688251123699712"
# Only used to pick the right league if the anchor manager is ever in more
# than one league in the same season; it does NOT drive what's shown on the
# page (see league_display_name(), which reads the live name off Sleeper).
LEAGUE_NAME_HINT = "dtf club"

# If auto-discovery ever fails (renamed league, API hiccup, etc.) fall back to
# the newest league ID and name known at the time this script was written.
FALLBACK_LEAGUE_ID = "1389416556617801728"  # 2026 season
FALLBACK_LEAGUE_NAME = "DTF Club"

MAX_WEEKS_TO_CHECK = 18

# Seasons whose playoff results are permanently hand-curated in
# playoff_baseline.tsv (includes the 3-team-championship years, back when
# the league was 10 teams). That format is retired -- now that the league is
# 12 teams, playoffs are a normal bracket again, so 2026 onward is pulled
# automatically from Sleeper's own bracket data instead of by hand.
FROZEN_PLAYOFF_SEASONS = {"2021", "2022", "2023", "2024", "2025"}

# The playoff bracket record actually goes back to 2010, but 2010 and 2011
# are incomplete: only the finals matchup and who reached it are known, not
# who either finalist beat in the semifinal round. Rather than guess an
# opponent, that semifinal win is credited to the finalist's career total
# with no head-to-head opponent attached (shown with an asterisk on the
# playoff table). Both years already have their known finals game recorded
# as a normal row in playoff_baseline.tsv (Ryan beat Alex in 2011, Jake beat
# Zak in 2010); this is only the unattached extra win each finalist gets for
# advancing out of a semifinal nobody recorded the opponent for. Keyed by
# season so the page can describe which years are incomplete on its own.
PLAYOFF_IMPLIED_WINS = {
    "2010": {"Jake": 1, "Zak": 1},
    "2011": {"Ryan": 1, "Alex": 1},
}

# Seasons that ended in a 3-team championship (highest score wins, the other
# two each take a loss) instead of a normal 1-on-1 final. Retired now that
# the league has grown to 12 teams. Used only to describe the table in
# natural language; the actual results are already expanded into normal
# rows in playoff_baseline.tsv.
PLAYOFF_THREE_TEAM_SEASONS = {"2023", "2024", "2025"}

# Sleeper user_id -> real manager name, verified against actual game results
# (not a guess). Anyone not listed here falls back to their Sleeper display
# name automatically, per instructions.
MANAGER_MAP = {
    "611688251123699712": "Ben",
    "473908924727160832": "Haan",
    "621896714877599744": "Steven",
    "622233730559348736": "Joe",
    "622284153274048512": "Aidan",
    "683108507217653760": "Jake",
    "734157966206455808": "Ankit",
    "735661741740056576": "Ryan",
    "740989628160614400": "Alex",
    "893989602594234368": "Christian",
    "992141374650753024": "Kaitlyn",
    "1389426153571229696": "Stephanie",
    "622238358105575424": "Tommy",   # no longer in the league
    "732065507691356160": "Oleg",    # no longer in the league
}


def fetch_json(path):
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "dtf-club-h2h/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"WARN: HTTP {e.code} fetching {url}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"WARN: failed to fetch {url}: {e}", file=sys.stderr)
        return None


def load_tsv_results(path, season_aware):
    """season_aware=True: rows are (Season, Week, Winner, Loser).
    season_aware=False: rows are (Winner, Loser)."""
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if season_aware:
                rows.append((r["Season"].strip(), int(r["Week"]), r["Winner"].strip(), r["Loser"].strip()))
            else:
                rows.append((r["Winner"].strip(), r["Loser"].strip()))
    return rows


def load_baseline():
    rows = load_tsv_results(BASELINE_PATH, season_aware=True)
    seen_weeks = defaultdict(set)
    for season, week, _w, _l in rows:
        seen_weeks[season].add(week)
    return rows, seen_weeks


def load_playoff_baseline():
    """Returns a list of (season:str, winner, loser). Unlike the regular
    season baseline, there's no Week column here -- just which season each
    playoff result happened in."""
    rows = []
    with open(PLAYOFF_BASELINE_PATH, newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows.append((r["Season"].strip(), r["Winner"].strip(), r["Loser"].strip()))
    return rows


def get_current_state():
    return fetch_json("/state/nfl")  # has 'season', 'week', 'season_type'


def discover_current_league_id():
    state = get_current_state()
    if state and state.get("season"):
        season = state["season"]
        leagues = fetch_json(f"/user/{ANCHOR_USER_ID}/leagues/nfl/{season}")
        if leagues:
            if len(leagues) == 1:
                return leagues[0]["league_id"], state
            for lg in leagues:
                if LEAGUE_NAME_HINT in (lg.get("name") or "").lower():
                    return lg["league_id"], state
            print("WARN: multiple leagues found for anchor user, no name match; "
                  "using the first one.", file=sys.stderr)
            return leagues[0]["league_id"], state
    print("WARN: could not auto-discover current league; using fallback ID.",
          file=sys.stderr)
    return FALLBACK_LEAGUE_ID, state


def get_league_chain(current_league_id):
    """Walk previous_league_id backward. Returns list of league dicts,
    oldest season first."""
    chain = []
    league_id = current_league_id
    visited = set()
    while league_id and league_id not in visited:
        visited.add(league_id)
        league = fetch_json(f"/league/{league_id}")
        if not league:
            break
        chain.append(league)
        league_id = league.get("previous_league_id")
    chain.reverse()
    return chain


def league_display_name(chain):
    """The league's actual current name on Sleeper (chain is oldest-first,
    so the newest season is last), so a rename shows up here on its own
    without editing this script."""
    if chain:
        name = (chain[-1].get("name") or "").strip()
        if name:
            return name
    return FALLBACK_LEAGUE_NAME


_roster_name_cache = {}

# Manual overrides for a specific (league_id, roster_id) whose Sleeper
# ownership record can no longer tell us who actually played it: when a
# manager leaves the league, Sleeper sometimes just sets that roster's
# owner_id to null rather than reassigning it, and once that happens there's
# no live API call that can recover who it was -- the historical link is
# just gone. Keyed by the league_id for the specific SEASON this applies to,
# since roster_id numbering is only unique within one league/season.
ROSTER_OVERRIDES = {
    # 2023 season: roster 9 was Tommy's all year (matches history_baseline.tsv);
    # Sleeper now reports it as owner_id null since he left the league.
    "995809919691444224": {9: "Tommy"},
}


def roster_owner_names(league_id):
    if league_id in _roster_name_cache:
        return _roster_name_cache[league_id]
    rosters = fetch_json(f"/league/{league_id}/rosters") or []
    users = fetch_json(f"/league/{league_id}/users") or []
    display_by_user = {u["user_id"]: u.get("display_name") or u["user_id"] for u in users}
    overrides = ROSTER_OVERRIDES.get(league_id, {})
    name_by_roster = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        roster_id = r.get("roster_id")
        if roster_id is None:
            continue
        if roster_id in overrides:
            name_by_roster[roster_id] = overrides[roster_id]
            continue
        if owner_id is None:
            continue
        name = MANAGER_MAP.get(owner_id, display_by_user.get(owner_id, owner_id))
        name_by_roster[roster_id] = name
    _roster_name_cache[league_id] = name_by_roster
    return name_by_roster


def completed_weeks_for_league(league_id, total_rosters, max_week_exclusive):
    """Yields (week, [(winner_name, loser_name, winner_pts, loser_pts), ...])
    for weeks where every roster played a normal 1-vs-1 game (the 'everyone
    plays' rule), stopping at the first week that isn't (playoffs/bye
    split), has no data yet, or exceeds max_week_exclusive."""
    name_by_roster = roster_owner_names(league_id)
    if not name_by_roster:
        return
    limit = min(MAX_WEEKS_TO_CHECK, max_week_exclusive - 1) if max_week_exclusive else MAX_WEEKS_TO_CHECK

    for week in range(1, limit + 1):
        data = fetch_json(f"/league/{league_id}/matchups/{week}")
        if not data:
            break  # not played yet (or season over)

        groups = defaultdict(list)
        fully_paired = True
        for entry in data:
            mid = entry.get("matchup_id")
            if mid is None:
                fully_paired = False
                break
            groups[mid].append(entry)

        if not fully_paired:
            break
        if any(len(v) != 2 for v in groups.values()):
            break
        if len(data) != total_rosters:
            break

        games = []
        for pair in groups.values():
            a, b = pair
            a_name = name_by_roster.get(a["roster_id"])
            b_name = name_by_roster.get(b["roster_id"])
            if a_name is None or b_name is None:
                continue
            if a["points"] == b["points"]:
                continue  # tie: doesn't fit a winner/loser record, skip
            if a["points"] > b["points"]:
                winner, loser, winner_pts, loser_pts = a_name, b_name, a["points"], b["points"]
            else:
                winner, loser, winner_pts, loser_pts = b_name, a_name, b["points"], a["points"]
            games.append((winner, loser, winner_pts, loser_pts))
        yield week, games


def gather_regular_season_data(chain, state, seen_weeks):
    """Walks every league in the chain and fetches every regular-season
    week's actual results (the same 'everyone plays' rule the baseline
    follows). Returns:
      - new_rows: (season, week, winner, loser) for weeks not already in
        the baseline -- this is the authoritative source for win/loss
        totals, unchanged from before.
      - scores_by_game: {(season:int, week:int, frozenset({a, b})):
        {a: points, b: points}} for every regular-season week reachable
        live, INCLUDING ones already recorded in the frozen baseline. This
        is purely to annotate the hover tooltips with real scores; it never
        changes any recorded win or loss (those still only ever come from
        the frozen baseline / these same new_rows, exactly as before).
    """
    new_rows = []
    scores_by_game = {}
    for league in chain:
        season = league["season"]
        league_id = league["league_id"]
        total_rosters = league.get("total_rosters")
        if not total_rosters:
            continue

        # Cap regular-season counting at whichever is more restrictive: the
        # league's own playoff_week_start (when set and > 0), or -- for the
        # season currently in progress -- the live week (never trust an
        # in-progress week's partial score as final).
        caps = []
        pws = (league.get("settings") or {}).get("playoff_week_start")
        if pws:
            caps.append(int(pws))
        if state and season == state.get("season") and state.get("week"):
            caps.append(int(state["week"]))
        max_week_exclusive = min(caps) if caps else None

        for week, games in completed_weeks_for_league(league_id, total_rosters, max_week_exclusive):
            for winner, loser, winner_pts, loser_pts in games:
                scores_by_game[(int(season), week, frozenset((winner, loser)))] = {
                    winner: winner_pts, loser: loser_pts,
                }
                if week not in seen_weeks.get(season, set()):
                    new_rows.append((season, week, winner, loser))
    return new_rows, scores_by_game


def gather_new_playoff_games(chain):
    """Auto-pull playoff results for any season not in FROZEN_PLAYOFF_SEASONS,
    straight from Sleeper's own bracket data (a normal winners bracket -- no
    3-team-final handling needed, since that format is retired). Only pulled
    once Sleeper marks the season 'complete', so an in-progress playoff round
    is never counted as final."""
    games = []
    for league in chain:
        season = league["season"]
        if season in FROZEN_PLAYOFF_SEASONS:
            continue
        if league.get("status") != "complete":
            continue  # postseason still in progress (or hasn't started) -- wait for it
        league_id = league["league_id"]
        bracket = fetch_json(f"/league/{league_id}/winners_bracket") or []
        if not bracket:
            continue
        name_by_roster = roster_owner_names(league_id)
        for m in bracket:
            w_id, l_id = m.get("w"), m.get("l")
            if w_id is None or l_id is None:
                continue  # this round wasn't actually decided (e.g. a bye slot)
            w_name = name_by_roster.get(w_id)
            l_name = name_by_roster.get(l_id)
            if w_name and l_name:
                games.append((season, w_name, l_name))
    return games


def build_matrix(pairs):
    """pairs: iterable of (winner, loser) tuples (season/week ignored)."""
    record = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    managers = set()
    for winner, loser in pairs:
        managers.add(winner)
        managers.add(loser)
        record[winner][loser][0] += 1
        record[loser][winner][1] += 1
    return record, sorted(managers)


def build_pair_histories(rows):
    """rows: (season, week, winner, loser) tuples. Returns a dict keyed by
    frozenset({a, b}) -> chronologically sorted list of (season:int,
    week:int, winner) for every game between that pair. Used to power the
    regular-season hover tooltips; not meaningful for the playoff table
    (no reliable week numbers / some seasons are hand-entered), so this is
    only ever built from regular_rows."""
    by_pair = defaultdict(list)
    for season, week, winner, loser in rows:
        by_pair[frozenset((winner, loser))].append((int(season), int(week), winner))
    for games in by_pair.values():
        games.sort(key=lambda g: (g[0], g[1]))
    return by_pair


def current_streak(games):
    """games: chronologically sorted list of (season, week, winner) for one
    pair. Returns (manager, count) for the trailing same-winner streak if
    it's 2 or more games, else None."""
    if len(games) < 2:
        return None
    last_winner = games[-1][2]
    count = 0
    for _season, _week, winner in reversed(games):
        if winner != last_winner:
            break
        count += 1
    return (last_winner, count) if count >= 2 else None


def render_tooltip_html(a, b, games, scores_by_game=None):
    """Chronological matchup history between a and b, oldest first, with a
    green trailing-streak line at the bottom when one manager has won 2 or
    more of the most recent games in a row. When scores_by_game has an
    entry for a given game (only reachable while the league that played it
    is still live on Sleeper), the actual score is shown alongside it;
    otherwise that line just shows who won, as before."""
    if not games:
        return None
    row_parts = []
    for season, week, winner in games:
        loser = b if winner == a else a
        score_txt = ""
        scores = (scores_by_game or {}).get((season, week, frozenset((a, b))))
        if scores and winner in scores and loser in scores:
            score_txt = f' ({scores[winner]:.1f}&ndash;{scores[loser]:.1f})'
        row_parts.append(f'<div class="tip-row">{season} Wk{week} &mdash; {html.escape(winner)}{score_txt}</div>')
    rows_html = "".join(row_parts)
    streak_html = ""
    streak = current_streak(games)
    if streak:
        manager, count = streak
        streak_html = f'<div class="tip-streak">{html.escape(manager)} has won {count} straight</div>'
    return f'<div class="tip-head">{html.escape(a)} vs. {html.escape(b)}</div>{rows_html}{streak_html}'


def natural_join(items):
    """['2010', '2011', '2012'] -> '2010, 2011 and 2012'."""
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def render_table_section(record, managers, section_id, title, subtitle, implied_wins=None,
                          incomplete_seasons=None, pair_histories=None, scores_by_game=None):
    implied_wins = implied_wins or {}
    managers = sorted(set(managers) | set(implied_wins))

    def cell(a, b):
        if a == b:
            return '<td class="diag">&mdash;</td>'
        w = record[a][b][0] if b in record.get(a, {}) else 0
        l = record[a][b][1] if b in record.get(a, {}) else 0
        total = w + l
        if total == 0:
            return '<td class="empty">&ndash;</td>'
        win_pct = w / total
        # diverging tint: blue = row manager dominates, red = column manager
        # dominates, gray midpoint = even. Intensity scales with margin.
        margin = win_pct - 0.5  # -0.5..0.5
        intensity = min(abs(margin) * 2, 1.0)  # 0..1
        if margin > 0:
            bg = f"rgba(42,120,214,{0.10 + 0.28 * intensity:.3f})"
        elif margin < 0:
            bg = f"rgba(227,73,72,{0.10 + 0.28 * intensity:.3f})"
        else:
            bg = "rgba(137,135,129,0.12)"

        attrs = [f'style="background:{bg}"']
        if pair_histories is not None:
            games = pair_histories.get(frozenset((a, b)))
            tip_html = render_tooltip_html(a, b, games, scores_by_game) if games else None
            if tip_html:
                attrs.append('class="tip-anchor"')
                attrs.append(f'data-tip="{html.escape(tip_html, quote=True)}"')
        return f'<td {" ".join(attrs)}><span class="rec">{w}-{l}</span></td>'

    def total_record(m):
        w = sum(v[0] for v in record.get(m, {}).values()) + implied_wins.get(m, 0)
        l = sum(v[1] for v in record.get(m, {}).values())
        return w, l

    totals = {m: total_record(m) for m in managers}
    ordered = sorted(managers, key=lambda m: (-totals[m][0], totals[m][1], m))
    any_implied = any(implied_wins.get(m) for m in ordered)

    header_cells = "".join(f'<th class="colhead">{m}</th>' for m in ordered)
    body_rows = []
    for m in ordered:
        w, l = totals[m]
        pct = f"{(w / (w + l) * 100):.1f}%" if (w + l) else "-"
        star = "*" if implied_wins.get(m) else ""
        row_cells = "".join(cell(m, other) for other in ordered)
        body_rows.append(
            f'<tr><th class="rowhead">{m}</th>{row_cells}'
            f'<td class="total">{w}-{l}{star}<span class="pct">{pct}</span></td></tr>'
        )
    body_html = "\n".join(body_rows)

    footnote = ""
    if any_implied:
        seasons_sorted = sorted(incomplete_seasons or [])
        years_text = natural_join(seasons_sorted)
        bracket_word = "bracket is" if len(seasons_sorted) == 1 else "brackets are"
        footnote = (
            f'<p class="footnote">The asterisk marks a win with no recorded opponent. '
            f"The {years_text} {bracket_word} incomplete: we only know who reached the "
            f"final and how the final turned out, not who each finalist beat in the "
            f"semifinal. That semifinal win still counts toward their career total, "
            f"it just isn't reflected in any single matchup cell above.</p>"
        )

    return f"""
  <section id="{section_id}">
  <h1>{title}</h1>
  <p class="subtitle">{subtitle}</p>
  <div class="table-wrap">
  <table>
    <thead><tr><th></th>{header_cells}<th>Total</th></tr></thead>
    <tbody>
{body_html}
    </tbody>
  </table>
  </div>
  {footnote}
  </section>
"""


PAGE_STYLE = """
  :root {
    color-scheme: light;
    --surface: #fcfcfb;
    --page: #f9f9f7;
    --ink: #0b0b0b;
    --ink-2: #52514e;
    --muted: #898781;
    --grid: #e1e0d9;
    --border: rgba(11,11,11,0.10);
    --pos: #1f9d55;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --surface: #1a1a19;
      --page: #0d0d0d;
      --ink: #ffffff;
      --ink-2: #c3c2b7;
      --muted: #898781;
      --grid: #2c2c2a;
      --border: rgba(255,255,255,0.10);
      --pos: #3fbd76;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 20px;
    background: var(--page);
    color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  section { margin-bottom: 32px; }
  h1 {
    font-size: 18px;
    font-weight: 600;
    margin: 0 0 2px 0;
  }
  .subtitle {
    color: var(--ink-2);
    font-size: 13px;
    margin: 0 0 16px 0;
  }
  .table-wrap {
    overflow-x: auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 4px;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }
  th, td {
    padding: 8px 10px;
    text-align: center;
    white-space: nowrap;
    border-bottom: 1px solid var(--grid);
    border-right: 1px solid var(--grid);
  }
  th.rowhead {
    text-align: left;
    font-weight: 600;
    position: sticky;
    left: 0;
    background: var(--surface);
    z-index: 1;
  }
  th.colhead {
    font-weight: 600;
    color: var(--ink-2);
    border-bottom: 2px solid var(--grid);
  }
  td.diag { color: var(--muted); }
  td.empty { color: var(--grid); }
  td.total {
    font-weight: 700;
    background: var(--page);
    border-left: 2px solid var(--grid);
  }
  .pct {
    display: block;
    font-weight: 400;
    color: var(--muted);
    font-size: 11px;
  }
  .rec { color: var(--ink); }
  .footnote {
    margin: 10px 2px 0 2px;
    color: var(--muted);
    font-size: 11px;
    max-width: 720px;
  }
  footer {
    margin-top: 14px;
    color: var(--muted);
    font-size: 11px;
  }
  .tip-anchor { cursor: help; }
  #cell-tooltip {
    position: fixed;
    display: none;
    z-index: 50;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 12px;
    line-height: 1.5;
    box-shadow: 0 8px 24px rgba(0,0,0,0.18);
    max-width: 240px;
    pointer-events: none;
  }
  #cell-tooltip .tip-head {
    font-weight: 700;
    margin-bottom: 6px;
    white-space: nowrap;
  }
  #cell-tooltip .tip-row {
    color: var(--ink-2);
    white-space: nowrap;
  }
  #cell-tooltip .tip-streak {
    margin-top: 6px;
    font-weight: 700;
    color: var(--pos);
    white-space: nowrap;
  }
"""

TOOLTIP_SCRIPT = """
<div id="cell-tooltip"></div>
<script>
(function () {
  var tip = document.getElementById("cell-tooltip");
  function position(e) {
    var pad = 14;
    var x = e.clientX + pad;
    var y = e.clientY + pad;
    var rect = tip.getBoundingClientRect();
    if (x + rect.width > window.innerWidth - 8) x = e.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 8) y = e.clientY - rect.height - pad;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  document.addEventListener("mouseover", function (e) {
    var td = e.target.closest(".tip-anchor");
    if (!td) return;
    tip.innerHTML = td.getAttribute("data-tip");
    tip.style.display = "block";
    position(e);
  });
  document.addEventListener("mousemove", function (e) {
    if (tip.style.display === "block" && e.target.closest(".tip-anchor")) position(e);
  });
  document.addEventListener("mouseout", function (e) {
    var td = e.target.closest(".tip-anchor");
    if (td && !td.contains(e.relatedTarget)) tip.style.display = "none";
  });
})();
</script>
"""


def render_html(league_name, regular_record, regular_managers, regular_rows,
                 playoff_record, playoff_managers, playoff_rows, generated_at,
                 scores_by_game=None):
    league_name = html.escape(league_name)
    reg_seasons = sorted({r[0] for r in regular_rows}, key=int)
    reg_first, reg_last = reg_seasons[0], reg_seasons[-1]
    reg_span = reg_first if reg_first == reg_last else f"{reg_first} through {reg_last}"
    regular_subtitle = (
        f"This is how every manager has fared against every other manager in the "
        f"regular season, covering {reg_span}. It's built from {len(regular_rows)} games "
        f"so far and updates itself automatically as new games are played."
    )
    # Hover tooltips (chronological matchup history + current streak) are a
    # regular-season-only feature: the playoff table has no reliable week
    # numbers for the hand-entered years, so it's deliberately left alone
    # (pair_histories is only ever passed to this one call).
    pair_histories = build_pair_histories(regular_rows)
    regular_section = render_table_section(
        regular_record, regular_managers, "regular-season",
        f"{league_name} – Regular Season Head-to-Head",
        regular_subtitle,
        pair_histories=pair_histories,
        scores_by_game=scores_by_game,
    )

    playoff_seasons = sorted({r[0] for r in playoff_rows}, key=int)
    playoff_first, playoff_last = playoff_seasons[0], playoff_seasons[-1]
    playoff_span = playoff_first if playoff_first == playoff_last else f"{playoff_first} through {playoff_last}"

    flat_implied_wins = defaultdict(int)
    for season_wins in PLAYOFF_IMPLIED_WINS.values():
        for manager, count in season_wins.items():
            flat_implied_wins[manager] += count
    incomplete_seasons = sorted(PLAYOFF_IMPLIED_WINS.keys(), key=int)

    three_team_seasons = sorted(PLAYOFF_THREE_TEAM_SEASONS & set(playoff_seasons), key=int)
    three_team_sentence = ""
    if three_team_seasons:
        three_team_sentence = (
            f" In {natural_join(three_team_seasons)}, the season ended with a three "
            f"team championship instead of a normal final: the highest score won it, "
            f"and the other two teams both took a loss. That format is retired now "
            f"that the league has grown to 12 teams."
        )

    frozen_last = max(FROZEN_PLAYOFF_SEASONS, key=int) if FROZEN_PLAYOFF_SEASONS else playoff_last
    automation_start = str(int(frozen_last) + 1)
    playoff_subtitle = (
        f"This is how every manager has fared against every other manager in the "
        f"postseason, covering {playoff_span}.{three_team_sentence} Starting in "
        f"{automation_start}, the playoffs went back to a normal bracket, so those "
        f"results are pulled in automatically the same way the regular season is. "
        f"{len(playoff_rows)} games recorded so far."
    )
    playoff_section = render_table_section(
        playoff_record, playoff_managers, "playoffs",
        f"{league_name} – Playoff Head-to-Head",
        playoff_subtitle,
        implied_wins=flat_implied_wins,
        incomplete_seasons=incomplete_seasons,
    )

    page_html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{league_name} &ndash; Head-to-Head Records</title>
<style>
{PAGE_STYLE}
</style>
</head>
<body>
{regular_section}
{playoff_section}
  <footer>Auto-updated {generated_at} from the Sleeper API. Playoff results through {frozen_last} are entered by hand; see the README for how to update them. Hover any regular-season cell for that pair's matchup history.</footer>
{TOOLTIP_SCRIPT}
</body>
</html>
"""
    OUTPUT_HTML.write_text(page_html)


def main():
    baseline_rows, seen_weeks = load_baseline()

    current_league_id, state = discover_current_league_id()
    chain = get_league_chain(current_league_id)
    league_name = league_display_name(chain)

    if chain:
        new_rows, scores_by_game = gather_regular_season_data(chain, state, seen_weeks)
    else:
        print("WARN: could not build league chain (offline/blocked?); "
              "rendering regular season from baseline only.", file=sys.stderr)
        new_rows, scores_by_game = [], {}

    all_regular_rows = baseline_rows + new_rows
    regular_pairs = [(w, l) for _s, _wk, w, l in all_regular_rows]
    regular_record, regular_managers = build_matrix(regular_pairs)

    playoff_baseline_rows = load_playoff_baseline()
    new_playoff_rows = gather_new_playoff_games(chain) if chain else []
    all_playoff_rows = playoff_baseline_rows + new_playoff_rows
    playoff_pairs = [(w, l) for _s, w, l in all_playoff_rows]
    playoff_record, playoff_managers = build_matrix(playoff_pairs)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    render_html(league_name, regular_record, regular_managers, all_regular_rows,
                playoff_record, playoff_managers, all_playoff_rows, generated_at,
                scores_by_game=scores_by_game)
    print(f"Wrote {OUTPUT_HTML} for '{league_name}': regular season {len(all_regular_rows)} games "
          f"({len(new_rows)} newly pulled, {len(scores_by_game)} with a real score) across "
          f"{len(regular_managers)} managers; playoffs {len(all_playoff_rows)} games "
          f"({len(new_playoff_rows)} newly pulled) across {len(playoff_managers)} managers.")


if __name__ == "__main__":
    main()
