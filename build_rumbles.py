#!/usr/bin/env python3
"""
DTF Club -- Rumbles standings history builder.

"Rumbles" is the league's custom weekly scoring system, on top of normal
head-to-head wins/losses:
  - Win your head-to-head matchup: 9 Rumbles.
  - +1 Rumble for every other team in the league you outscore that week
    (a round-robin "vs. the field" bonus). Since winning your matchup means
    you outscored that opponent too, a normal head-to-head win is worth at
    least 10 Rumbles (9 + 1), up to 20 if you also had the highest score in
    the league that week (9 + 11, with 12 teams).

This script computes the ACTUAL Rumbles standings for every fully completed
week of the current season only (this is a season-long standings system, not
a career one) and writes the running totals to rumbles_history.json:
  - Rank, cumulative Rumbles, Rumble %, PF, PA, head-to-head W-L, and
    vs.-field W-L for every manager, through the last fully completed week.

It deliberately stops at the last COMPLETE week (Sleeper's current live week
minus one) -- an in-progress week's scores aren't final yet, so it's left for
the live page (rumbles.html) to compute client-side in real time, straight
from the browser, using actual-so-far plus projections for anyone who hasn't
played yet. This script never touches that in-progress week.

No API key needed (Sleeper's API is public read-only).
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Reuse the already-verified league discovery / roster-naming machinery from
# the head-to-head builder instead of duplicating it.
import build_matrix as h2h

HERE = Path(__file__).resolve().parent
OUTPUT_JSON = HERE / "rumbles_history.json"

RUMBLES_PER_WIN = 9


def fetch_week_scores(league_id, week, name_by_roster):
    """Returns None if the week isn't playable/available yet or isn't a full
    round-robin week (playoffs/bye split already started). Otherwise returns
    a list of dicts: {roster_id, manager, points, matchup_id}."""
    data = h2h.fetch_json(f"/league/{league_id}/matchups/{week}")
    if not data:
        return None

    groups = defaultdict(list)
    for entry in data:
        mid = entry.get("matchup_id")
        if mid is None:
            return None  # bye/consolation split already underway
        groups[mid].append(entry)
    if any(len(v) != 2 for v in groups.values()):
        return None

    rows = []
    for entry in data:
        roster_id = entry.get("roster_id")
        manager = name_by_roster.get(roster_id)
        if manager is None:
            continue
        rows.append({
            "roster_id": roster_id,
            "manager": manager,
            "points": float(entry.get("points") or 0.0),
            "matchup_id": entry.get("matchup_id"),
        })
    if len(rows) != len(name_by_roster):
        return None
    return rows


def rumbles_for_week(week_rows):
    """week_rows: list of {roster_id, manager, points, matchup_id}.
    Returns list of dicts with rumbles/h2h/field results added, per manager."""
    by_matchup = defaultdict(list)
    for row in week_rows:
        by_matchup[row["matchup_id"]].append(row)

    h2h_result = {}  # manager -> "W"/"L"/"T"
    for pair in by_matchup.values():
        if len(pair) != 2:
            continue
        a, b = pair
        if a["points"] > b["points"]:
            h2h_result[a["manager"]] = "W"
            h2h_result[b["manager"]] = "L"
        elif b["points"] > a["points"]:
            h2h_result[b["manager"]] = "W"
            h2h_result[a["manager"]] = "L"
        else:
            h2h_result[a["manager"]] = "T"
            h2h_result[b["manager"]] = "T"

    results = []
    for row in week_rows:
        me = row["manager"]
        beaten = sum(
            1 for other in week_rows
            if other["manager"] != me and row["points"] > other["points"]
        )
        outcome = h2h_result.get(me, "T")
        rumbles = (RUMBLES_PER_WIN if outcome == "W" else 0) + beaten
        results.append({
            "manager": me,
            "points": row["points"],
            "h2h": outcome,
            "field_w": beaten,
            "field_l": (len(week_rows) - 1) - beaten,
            "rumbles": rumbles,
        })
    return results


def build_history():
    current_league_id, state = h2h.discover_current_league_id()
    chain = h2h.get_league_chain(current_league_id)
    if not chain:
        print("WARN: could not build league chain (offline/blocked?); "
              "no rumbles history written.", file=sys.stderr)
        return None

    league = chain[-1]  # current season is always last in the oldest-first chain
    league_id = league["league_id"]
    league_name = h2h.league_display_name(chain)
    total_rosters = league.get("total_rosters")
    name_by_roster = h2h.roster_owner_names(league_id)

    pws = (league.get("settings") or {}).get("playoff_week_start")
    caps = []
    if pws:
        caps.append(int(pws))
    live_week = int(state["week"]) if state and state.get("week") else None
    if live_week:
        caps.append(live_week)
    max_week_exclusive = min(caps) if caps else h2h.MAX_WEEKS_TO_CHECK + 1

    totals = defaultdict(lambda: {
        "rumbles": 0, "pf": 0.0, "pa": 0.0,
        "h2h_w": 0, "h2h_l": 0, "h2h_t": 0,
        "field_w": 0, "field_l": 0, "weeks_played": 0,
    })
    weeks_included = []

    for week in range(1, min(max_week_exclusive, h2h.MAX_WEEKS_TO_CHECK + 1)):
        rows = fetch_week_scores(league_id, week, name_by_roster)
        if rows is None:
            break  # not played / not a full round-robin week yet
        week_results = rumbles_for_week(rows)
        pa_by_manager = {}
        for pair in defaultdict(list, {
            r["matchup_id"]: [x for x in rows if x["matchup_id"] == r["matchup_id"]]
            for r in rows
        }).values():
            if len(pair) == 2:
                a, b = pair
                pa_by_manager[a["manager"]] = b["points"]
                pa_by_manager[b["manager"]] = a["points"]

        for r in week_results:
            t = totals[r["manager"]]
            t["rumbles"] += r["rumbles"]
            t["pf"] += r["points"]
            t["pa"] += pa_by_manager.get(r["manager"], 0.0)
            t["weeks_played"] += 1
            if r["h2h"] == "W":
                t["h2h_w"] += 1
            elif r["h2h"] == "L":
                t["h2h_l"] += 1
            else:
                t["h2h_t"] += 1
            t["field_w"] += r["field_w"]
            t["field_l"] += r["field_l"]
        weeks_included.append(week)

    through_week = weeks_included[-1] if weeks_included else 0
    max_possible_per_week = 9 + (total_rosters - 1) if total_rosters else 0

    teams = {}
    for manager, t in totals.items():
        max_possible = max_possible_per_week * t["weeks_played"]
        rumble_pct = (t["rumbles"] / max_possible) if max_possible else 0.0
        teams[manager] = {
            **t,
            "roster_id": next((rid for rid, name in name_by_roster.items() if name == manager), None),
            "rumble_pct": round(rumble_pct, 4),
        }

    return {
        "season": league.get("season"),
        "league_id": league_id,
        "league_name": league_name,
        "total_rosters": total_rosters,
        "through_week": through_week,
        "current_week": live_week,
        "max_possible_per_week": max_possible_per_week,
        "rosters": {str(rid): name for rid, name in name_by_roster.items()},
        "teams": teams,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def main():
    history = build_history()
    if history is None:
        return
    OUTPUT_JSON.write_text(json.dumps(history, indent=2, sort_keys=True))
    print(f"Wrote {OUTPUT_JSON}: season {history['season']}, through week "
          f"{history['through_week']} (live week {history['current_week']}), "
          f"{len(history['teams'])} teams.")


if __name__ == "__main__":
    main()
