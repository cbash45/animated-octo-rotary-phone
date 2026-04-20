"""
NBA Box Score Scraper
Pulls raw game data from stats.nba.com (free, no API key needed)
Stores into Supabase PostgreSQL database

Run manually or on a cron schedule (e.g. nightly at 2am)
"""

import os
import time
import json
import requests
from datetime import datetime, timedelta
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# NBA stats.nba.com headers (required — they block requests without these)
HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}

SEASON = "2025-26"
SEASON_TYPE = "Regular Season"  # or "Playoffs"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Fetch helpers ─────────────────────────────────────────────────────────────
def nba_get(endpoint, params):
    """Make a request to stats.nba.com with retry logic."""
    url = f"https://stats.nba.com/stats/{endpoint}"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  Attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    return None


def parse_response(data, result_set_index=0):
    """Convert NBA API response into list of dicts."""
    if not data:
        return []
    rs = data["resultSets"][result_set_index]
    headers = rs["headers"]
    rows = rs["rowSet"]
    return [dict(zip(headers, row)) for row in rows]


# ── Fetch game log ────────────────────────────────────────────────────────────
def fetch_game_log(season=SEASON, season_type=SEASON_TYPE):
    """
    Fetch full season game log — every game result with team box score stats.
    Returns list of game dicts.
    """
    print(f"Fetching game log for {season} {season_type}...")

    data = nba_get("leaguegamelog", {
        "Counter": 1000,
        "DateFrom": "",
        "DateTo": "",
        "Direction": "ASC",
        "LeagueID": "00",
        "PlayerOrTeam": "T",   # T = team stats, P = player stats
        "Season": season,
        "SeasonType": season_type,
        "Sorter": "DATE",
    })

    games = parse_response(data)
    print(f"  Found {len(games)} team-game records")
    return games


# ── Fetch team advanced stats ─────────────────────────────────────────────────
def fetch_team_advanced(season=SEASON, season_type=SEASON_TYPE):
    """
    Fetch season-level advanced stats per team:
    ORtg, DRtg, Pace, etc.
    Used as baseline ratings.
    """
    print(f"Fetching advanced team stats...")

    data = nba_get("leaguedashteamstats", {
        "Conference": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "GameScope": "",
        "GameSegment": "",
        "LastNGames": 0,
        "LeagueID": "00",
        "Location": "",
        "MeasureType": "Advanced",
        "Month": 0,
        "OpponentTeamID": 0,
        "Outcome": "",
        "PORound": 0,
        "PaceAdjust": "N",
        "PerMode": "PerGame",
        "Period": 0,
        "PlayerExperience": "",
        "PlayerPosition": "",
        "PlusMinus": "N",
        "Rank": "N",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": season_type,
        "ShotClockRange": "",
        "StarterBench": "",
        "TeamID": 0,
        "TwoWay": 0,
        "VsConference": "",
        "VsDivision": "",
    })

    teams = parse_response(data)
    print(f"  Found stats for {len(teams)} teams")
    return teams


# ── Calculations ──────────────────────────────────────────────────────────────
def calc_possessions(fga, fta, oreb, tov):
    """
    Estimate possessions using the standard formula.
    possessions ≈ FGA + 0.44*FTA - OREB + TOV
    """
    return fga + 0.44 * fta - oreb + tov


def calc_ortg(pts, poss):
    """Points scored per 100 possessions."""
    if poss == 0:
        return 0
    return round(100 * pts / poss, 1)


def calc_drtg(opp_pts, poss):
    """Points allowed per 100 possessions."""
    if poss == 0:
        return 0
    return round(100 * opp_pts / poss, 1)


def calc_pace(poss, minutes):
    """
    Possessions per 48 minutes (standard NBA game length).
    minutes is total team minutes played.
    """
    if minutes == 0:
        return 0
    return round((poss / minutes) * 48, 1)


# ── Elo calculation ───────────────────────────────────────────────────────────
def expected_score(elo_a, elo_b):
    """Expected win probability for team A vs team B."""
    return 1 / (1 + 10 ** ((elo_b - elo_a) / 400))


def margin_of_victory_multiplier(margin, elo_diff):
    """
    538-style MOV multiplier — rewards winning by more,
    but with diminishing returns to prevent running up score.
    """
    import math
    mov = abs(margin)
    # Autocorrelation correction (from 538 methodology)
    autocorr = 2.2 / (elo_diff * 0.001 + 2.2)
    return math.log(mov + 1) * autocorr


def update_elo(winner_elo, loser_elo, margin, k=20):
    """
    Update Elo ratings after a game.
    Returns (new_winner_elo, new_loser_elo)
    k=20 is standard for NBA (higher = more reactive to recent results)
    """
    exp = expected_score(winner_elo, loser_elo)
    elo_diff = abs(winner_elo - loser_elo)
    mov_mult = margin_of_victory_multiplier(margin, elo_diff)

    new_winner = winner_elo + k * mov_mult * (1 - exp)
    new_loser = loser_elo + k * mov_mult * (0 - (1 - exp))

    return round(new_winner, 1), round(new_loser, 1)


# ── Process and store games ───────────────────────────────────────────────────
def process_season(season=SEASON, season_type=SEASON_TYPE):
    """
    Full pipeline:
    1. Fetch all game logs
    2. Calculate rolling Elo for each game
    3. Store raw games + team ratings in Supabase
    """

    games_raw = fetch_game_log(season, season_type)
    if not games_raw:
        print("No games found, exiting.")
        return

    # Build paired games (home + away in same dict)
    # NBA game log returns one row per team per game
    # We need to pair them by GAME_ID
    game_map = {}
    for row in games_raw:
        gid = row["GAME_ID"]
        if gid not in game_map:
            game_map[gid] = []
        game_map[gid].append(row)

    # Initialize Elo ratings — all teams start at 1500
    elo_ratings = {}
    team_names = {}

    # Sort games chronologically
    sorted_game_ids = sorted(game_map.keys())

    games_to_insert = []
    elo_history = []

    print(f"\nProcessing {len(sorted_game_ids)} games...")

    for gid in sorted_game_ids:
        pair = game_map[gid]
        if len(pair) != 2:
            continue  # Skip incomplete data

        # Identify home vs away (MATCHUP contains 'vs.' for home, '@' for away)
        home = next((g for g in pair if "vs." in g.get("MATCHUP", "")), None)
        away = next((g for g in pair if "@" in g.get("MATCHUP", "")), None)

        if not home or not away:
            continue

        home_id = str(home["TEAM_ID"])
        away_id = str(away["TEAM_ID"])

        # Store team names
        team_names[home_id] = home["TEAM_NAME"]
        team_names[away_id] = away["TEAM_NAME"]

        # Initialize Elo if first time seeing team
        if home_id not in elo_ratings:
            elo_ratings[home_id] = 1500.0
        if away_id not in elo_ratings:
            elo_ratings[away_id] = 1500.0

        home_elo_before = elo_ratings[home_id]
        away_elo_before = elo_ratings[away_id]

        # Raw stats
        home_pts = home.get("PTS", 0) or 0
        away_pts = away.get("PTS", 0) or 0
        margin = home_pts - away_pts

        # Calculate possessions for each team
        home_fga  = home.get("FGA", 0) or 0
        home_fta  = home.get("FTA", 0) or 0
        home_oreb = home.get("OREB", 0) or 0
        home_tov  = home.get("TOV", 0) or 0
        away_fga  = away.get("FGA", 0) or 0
        away_fta  = away.get("FTA", 0) or 0
        away_oreb = away.get("OREB", 0) or 0
        away_tov  = away.get("TOV", 0) or 0

        home_poss = calc_possessions(home_fga, home_fta, home_oreb, home_tov)
        away_poss = calc_possessions(away_fga, away_fta, away_oreb, away_tov)
        avg_poss  = (home_poss + away_poss) / 2

        # Calculate ratings for this game
        home_ortg = calc_ortg(home_pts, avg_poss)
        home_drtg = calc_drtg(away_pts, avg_poss)
        away_ortg = calc_ortg(away_pts, avg_poss)
        away_drtg = calc_drtg(home_pts, avg_poss)

        # Pace (possessions per 48 min)
        # MIN field is in "MM:SS" format
        def parse_minutes(min_str):
            try:
                parts = str(min_str).split(":")
                return int(parts[0]) + int(parts[1]) / 60
            except:
                return 48.0

        home_min  = parse_minutes(home.get("MIN", "48:00"))
        home_pace = calc_pace(avg_poss, home_min)

        # Update Elo
        if margin > 0:
            new_h_elo, new_a_elo = update_elo(home_elo_before, away_elo_before, margin)
        elif margin < 0:
            new_a_elo, new_h_elo = update_elo(away_elo_before, home_elo_before, abs(margin))
        else:
            new_h_elo = home_elo_before
            new_a_elo = away_elo_before

        elo_ratings[home_id] = new_h_elo
        elo_ratings[away_id] = new_a_elo

        game_date = home.get("GAME_DATE", "")

        # Build game record
        game_record = {
            "game_id":       gid,
            "game_date":     game_date,
            "season":        season,
            "season_type":   season_type,
            "home_team_id":  home_id,
            "home_team":     home["TEAM_NAME"],
            "away_team_id":  away_id,
            "away_team":     away["TEAM_NAME"],
            "home_pts":      home_pts,
            "away_pts":      away_pts,
            "margin":        margin,
            "home_poss":     round(home_poss, 1),
            "away_poss":     round(away_poss, 1),
            "home_ortg":     home_ortg,
            "home_drtg":     home_drtg,
            "home_pace":     home_pace,
            "away_ortg":     away_ortg,
            "away_drtg":     away_drtg,
            "home_elo_pre":  home_elo_before,
            "away_elo_pre":  away_elo_before,
            "home_elo_post": new_h_elo,
            "away_elo_post": new_a_elo,
            "home_fga":      home_fga,
            "home_fta":      home_fta,
            "home_oreb":     home_oreb,
            "home_tov":      home_tov,
            "away_fga":      away_fga,
            "away_fta":      away_fta,
            "away_oreb":     away_oreb,
            "away_tov":      away_tov,
        }
        games_to_insert.append(game_record)

    print(f"Inserting {len(games_to_insert)} games into database...")

    # Upsert games in batches of 100
    for i in range(0, len(games_to_insert), 100):
        batch = games_to_insert[i:i+100]
        supabase.table("nba_games").upsert(batch, on_conflict="game_id").execute()
        print(f"  Inserted batch {i//100 + 1}")

    # ── Build rolling team ratings ──────────────────────────────────────────
    # Now compute rolling averages over last N games for each team
    print("\nCalculating rolling team ratings...")
    team_ratings = build_team_ratings(games_to_insert, elo_ratings, team_names)

    print(f"Upserting {len(team_ratings)} team ratings...")
    for i in range(0, len(team_ratings), 50):
        batch = team_ratings[i:i+50]
        supabase.table("nba_team_ratings").upsert(batch, on_conflict="team_id").execute()

    print("\n✓ Pipeline complete!")
    print(f"  Games processed: {len(games_to_insert)}")
    print(f"  Teams rated:     {len(team_ratings)}")


def build_team_ratings(games, current_elo, team_names, window=15):
    """
    Build current team ratings from rolling window of last N games.
    window=15 games gives a good balance of recency vs stability.
    """
    from collections import defaultdict

    # Collect each team's games in order
    team_games = defaultdict(list)
    for g in games:
        team_games[g["home_team_id"]].append({
            "date":  g["game_date"],
            "ortg":  g["home_ortg"],
            "drtg":  g["home_drtg"],
            "pace":  g["home_pace"],
            "pts":   g["home_pts"],
            "opp_pts": g["away_pts"],
        })
        team_games[g["away_team_id"]].append({
            "date":  g["game_date"],
            "ortg":  g["away_ortg"],
            "drtg":  g["away_drtg"],
            "pace":  g["home_pace"],  # pace is symmetric
            "pts":   g["away_pts"],
            "opp_pts": g["home_pts"],
        })

    ratings = []
    for team_id, tgames in team_games.items():
        # Sort by date
        tgames.sort(key=lambda x: x["date"])
        recent = tgames[-window:]  # last N games

        if not recent:
            continue

        n = len(recent)
        avg_ortg    = round(sum(g["ortg"] for g in recent) / n, 1)
        avg_drtg    = round(sum(g["drtg"] for g in recent) / n, 1)
        avg_pace    = round(sum(g["pace"] for g in recent) / n, 1)
        avg_pts     = round(sum(g["pts"] for g in recent) / n, 1)
        avg_opp_pts = round(sum(g["opp_pts"] for g in recent) / n, 1)
        net_rtg     = round(avg_ortg - avg_drtg, 1)

        wins  = sum(1 for g in tgames if g["pts"] > g["opp_pts"])
        losses = len(tgames) - wins
        record = f"{wins}-{losses}"

        ratings.append({
            "team_id":        team_id,
            "team_name":      team_names.get(team_id, "Unknown"),
            "elo":            round(current_elo.get(team_id, 1500), 1),
            "ortg":           avg_ortg,
            "drtg":           avg_drtg,
            "pace":           avg_pace,
            "net_rtg":        net_rtg,
            "avg_pts":        avg_pts,
            "avg_opp_pts":    avg_opp_pts,
            "games_played":   len(tgames),
            "wins":           wins,
            "losses":         losses,
            "record":         record,
            "window_games":   n,
            "updated_at":     datetime.utcnow().isoformat(),
        })

    return ratings


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("NBA Data Pipeline")
    print(f"Season: {SEASON} | Type: {SEASON_TYPE}")
    print("=" * 60)
    process_season(SEASON, SEASON_TYPE)
