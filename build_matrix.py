#!/usr/bin/env python3
"""
DTF Club - career head-to-head record builder.

What this does, every time it runs:
  1. Loads the frozen regular-season baseline (history_baseline.tsv) exactly
     as compiled by the league -- this file is never rewritten automatically,
     so past seasons' numbers never silently change.
  2. Auto-discovers this season's Sleeper league (no league ID to maintain --
     it looks up the anchor manager's leagues for the current NFL season and
     picks the one named "DTF Club"), then walks previous_league_id backward
     to confirm the season chain.
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
  4. Loads the frozen playoff baseline (playoff_baseline.tsv) -- career
     head-to-head results from the postseason, including the years with a
     3-team championship (highest score wins, the other two each take a loss).
     Not auto-updated yet; add new playoff results here by hand each year
     after the season wraps (see README).
  5. Recomputes both career win-loss matrices and renders index.html with
     both tables.

No API key needed (Sleeper's API is public read-only). No state is kept
between runs other than git history -- every run recomputes everything from
scratch, so it's self-healing.
"""

import csv
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
LEAGUE_NAME_HINT = "dtf club"

# If auto-discovery ever fails (renamed league, API hiccup, etc.) fall back to
# the newest league ID known at the time this script was written.
FALLBACK_LEAGUE_ID = "1389416556617801728"  # 2026 season

MAX_WEEKS_TO_CHECK = 18

# Seasons whose playoff results are permanently hand-curated in
# playoff_baseline.tsv (includes the 3-team-championship years, back when
# the league was 10 teams). That format is retired -- now that the league is
# 12 teams, playoffs are a normal bracket again, so 2026 onward is pulled
# automatically from Sleeper's own bracket data instead of by hand.
FROZEN_PLAYOFF_SEASONS = {"2021", "2022", "2023", "2024", "2025"}

# The playoff bracket record actually goes back to 2010, but 2010 and 2011
# are incomplete -- only the finals matchup and who reached it are known,
# not who either finalist beat in the semifinal round. Rather than guess an
# opponent, that semifinal win is credited to the finalist's career total
# with no head-to-head opponent attached (shown with an asterisk on the
# playoff table). Both 2010 and 2011 already have their known finals game
# recorded as a normal row in playoff_baseline.tsv (Ryan beat Alex in 2011;
# Jake beat Zak in 2010) -- this dict is only the *unattached* extra win for
# each finalist from advancing out of an unknown semifinal.
PLAYOFF_IMPLIED_WINS = {
    "Ryan": 1,   # 2011: won an unknown semifinal to reach the final
    "Alex": 1,   # 2011: won an unknown semifinal to reach the final
    "Jake": 1,   # 2010: won an unknown semifinal to reach the final
    "Zak": 1,    # 2010: won an unknown semifinal to reach the final
}

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


_roster_name_cache = {}


def roster_owner_names(league_id):
    if league_id in _roster_name_cache:
        return _roster_name_cache[league_id]
    rosters = fetch_json(f"/league/{league_id}/rosters") or []
    users = fetch_json(f"/league/{league_id}/users") or []
    display_by_user = {u["user_id"]: u.get("display_name") or u["user_id"] for u in users}
    name_by_roster = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        roster_id = r.get("roster_id")
        if owner_id is None or roster_id is None:
            continue
        name = MANAGER_MAP.get(owner_id, display_by_user.get(owner_id, owner_id))
        name_by_roster[roster_id] = name
    _roster_name_cache[league_id] = name_by_roster
    return name_by_roster


def completed_weeks_for_league(league_id, total_rosters, max_week_exclusive):
    """Yields (week, [(winner_name, loser_name), ...]) for weeks where every
    roster played a normal 1-vs-1 game (the 'everyone plays' rule), stopping
    at the first week that isn't (playoffs/bye split), has no data yet, or
    exceeds max_week_exclusive."""
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
            winner, loser = (a_name, b_name) if a["points"] > b["points"] else (b_name, a_name)
            games.append((winner, loser))
        yield week, games


def gather_new_games(chain, state, seen_weeks):
    """For every league in the chain, add any (season, week) not already in
    the baseline."""
    new_rows = []
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
            if week in seen_weeks.get(season, set()):
                continue
            for winner, loser in games:
                new_rows.append((season, week, winner, loser))
    return new_rows


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
                games.append((w_name, l_name))
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


def render_table_section(record, managers, section_id, title, subtitle, implied_wins=None):
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
        return f'<td style="background:{bg}"><span class="rec">{w}-{l}</span></td>'

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
        footnote = (
            '<p class="footnote">* includes a win with no recorded opponent '
            "-- 2010 and 2011's brackets are incomplete: only who reached the "
            "final and the final's result are known, not who each finalist "
            "beat in the semifinal, so that semifinal win counts toward "
            "their career total but isn't reflected in any single matchup "
            "cell above.</p>"
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
"""


def render_html(regular_record, regular_managers, regular_rows,
                 playoff_record, playoff_managers, playoff_pairs, generated_at):
    last_season = max((r[0] for r in regular_rows), default="-")
    regular_section = render_table_section(
        regular_record, regular_managers, "regular-season",
        "DTF Club – Regular Season Head-to-Head",
        f"Row manager's record vs. column manager, {last_season} season and earlier "
        f"&middot; {len(regular_rows)} games tracked",
    )
    playoff_section = render_table_section(
        playoff_record, playoff_managers, "playoffs",
        "DTF Club – Playoff Head-to-Head",
        "Row manager's record vs. column manager in the postseason, 2010 onward "
        "(2010 &amp; 2011 are incomplete -- only the finals are fully known) "
        "&middot; three years had a 3-team championship (highest score wins, "
        "the other two each take a loss) &mdash; retired now that the league is "
        f"12 teams; 2026 onward is a normal bracket, pulled automatically "
        f"&middot; {len(playoff_pairs)} recorded games",
        implied_wins=PLAYOFF_IMPLIED_WINS,
    )

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DTF Club &ndash; Head-to-Head Records</title>
<style>
{PAGE_STYLE}
</style>
</head>
<body>
{regular_section}
{playoff_section}
  <footer>Auto-updated {generated_at} from the Sleeper API &middot; 2010–2025 playoff results are hand-entered (see README)</footer>
</body>
</html>
"""
    OUTPUT_HTML.write_text(html)


def main():
    baseline_rows, seen_weeks = load_baseline()

    current_league_id, state = discover_current_league_id()
    chain = get_league_chain(current_league_id)

    if chain:
        new_rows = gather_new_games(chain, state, seen_weeks)
    else:
        print("WARN: could not build league chain (offline/blocked?); "
              "rendering regular season from baseline only.", file=sys.stderr)
        new_rows = []

    all_regular_rows = baseline_rows + new_rows
    regular_pairs = [(w, l) for _s, _wk, w, l in all_regular_rows]
    regular_record, regular_managers = build_matrix(regular_pairs)

    playoff_pairs = load_tsv_results(PLAYOFF_BASELINE_PATH, season_aware=False)
    new_playoff_pairs = gather_new_playoff_games(chain) if chain else []
    playoff_pairs = playoff_pairs + new_playoff_pairs
    playoff_record, playoff_managers = build_matrix(playoff_pairs)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    render_html(regular_record, regular_managers, all_regular_rows,
                playoff_record, playoff_managers, playoff_pairs, generated_at)
    print(f"Wrote {OUTPUT_HTML}: regular season {len(all_regular_rows)} games "
          f"({len(new_rows)} newly pulled) across {len(regular_managers)} managers; "
          f"playoffs {len(playoff_pairs)} games "
          f"({len(new_playoff_pairs)} newly pulled) across {len(playoff_managers)} managers.")


if __name__ == "__main__":
    main()
