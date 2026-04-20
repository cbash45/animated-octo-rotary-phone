-- ============================================================
-- NBA Analytics Database Schema
-- Run this in your Supabase SQL editor to set up the tables
-- ============================================================

-- ── Raw game results ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nba_games (
    game_id         TEXT PRIMARY KEY,
    game_date       DATE NOT NULL,
    season          TEXT NOT NULL,
    season_type     TEXT NOT NULL,

    -- Teams
    home_team_id    TEXT NOT NULL,
    home_team       TEXT NOT NULL,
    away_team_id    TEXT NOT NULL,
    away_team       TEXT NOT NULL,

    -- Score
    home_pts        INTEGER,
    away_pts        INTEGER,
    margin          INTEGER,         -- home_pts - away_pts (positive = home won)

    -- Possessions (calculated)
    home_poss       NUMERIC(5,1),
    away_poss       NUMERIC(5,1),

    -- Per-game ratings (calculated from this game)
    home_ortg       NUMERIC(5,1),    -- points scored per 100 possessions
    home_drtg       NUMERIC(5,1),    -- points allowed per 100 possessions
    home_pace       NUMERIC(5,1),    -- possessions per 48 minutes
    away_ortg       NUMERIC(5,1),
    away_drtg       NUMERIC(5,1),

    -- Elo (before and after this game)
    home_elo_pre    NUMERIC(7,1),
    away_elo_pre    NUMERIC(7,1),
    home_elo_post   NUMERIC(7,1),
    away_elo_post   NUMERIC(7,1),

    -- Raw box score components
    home_fga        INTEGER,
    home_fta        INTEGER,
    home_oreb       INTEGER,
    home_tov        INTEGER,
    away_fga        INTEGER,
    away_fta        INTEGER,
    away_oreb       INTEGER,
    away_tov        INTEGER,

    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast team lookups
CREATE INDEX IF NOT EXISTS idx_nba_games_home_team ON nba_games(home_team_id, game_date DESC);
CREATE INDEX IF NOT EXISTS idx_nba_games_away_team ON nba_games(away_team_id, game_date DESC);
CREATE INDEX IF NOT EXISTS idx_nba_games_date      ON nba_games(game_date DESC);
CREATE INDEX IF NOT EXISTS idx_nba_games_season    ON nba_games(season, season_type);


-- ── Current team ratings (updated nightly) ───────────────────
CREATE TABLE IF NOT EXISTS nba_team_ratings (
    team_id         TEXT PRIMARY KEY,
    team_name       TEXT NOT NULL,

    -- Core model inputs
    elo             NUMERIC(7,1),    -- current Elo rating
    ortg            NUMERIC(5,1),    -- rolling avg offensive rating
    drtg            NUMERIC(5,1),    -- rolling avg defensive rating
    pace            NUMERIC(5,1),    -- rolling avg pace
    net_rtg         NUMERIC(5,1),    -- ortg - drtg

    -- Season totals
    avg_pts         NUMERIC(5,1),    -- avg points scored
    avg_opp_pts     NUMERIC(5,1),    -- avg points allowed
    games_played    INTEGER,
    wins            INTEGER,
    losses          INTEGER,
    record          TEXT,            -- e.g. "54-28"
    window_games    INTEGER,         -- number of games used for rolling avg

    updated_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ── Elo history (one row per game per team) ───────────────────
-- Useful for tracking Elo over time, charting team trajectories
CREATE TABLE IF NOT EXISTS nba_elo_history (
    id              BIGSERIAL PRIMARY KEY,
    game_id         TEXT REFERENCES nba_games(game_id),
    game_date       DATE,
    team_id         TEXT,
    team_name       TEXT,
    elo_before      NUMERIC(7,1),
    elo_after       NUMERIC(7,1),
    elo_change      NUMERIC(6,1),
    opponent_id     TEXT,
    home_away       TEXT,            -- 'home' or 'away'
    result          TEXT,            -- 'W' or 'L'
    margin          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_elo_history_team ON nba_elo_history(team_id, game_date DESC);


-- ── Useful views ──────────────────────────────────────────────

-- Current standings ordered by Elo
CREATE OR REPLACE VIEW nba_standings AS
SELECT
    team_name,
    record,
    wins,
    losses,
    elo,
    ortg,
    drtg,
    net_rtg,
    pace,
    games_played,
    updated_at
FROM nba_team_ratings
ORDER BY elo DESC;


-- Recent form: last 10 games for each team
CREATE OR REPLACE VIEW nba_recent_form AS
WITH team_game_union AS (
    SELECT
        home_team_id    AS team_id,
        home_team       AS team_name,
        game_date,
        home_pts        AS pts,
        away_pts        AS opp_pts,
        CASE WHEN home_pts > away_pts THEN 'W' ELSE 'L' END AS result,
        home_ortg       AS ortg,
        home_drtg       AS drtg,
        home_elo_post   AS elo
    FROM nba_games
    UNION ALL
    SELECT
        away_team_id,
        away_team,
        game_date,
        away_pts,
        home_pts,
        CASE WHEN away_pts > home_pts THEN 'W' ELSE 'L' END,
        away_ortg,
        away_drtg,
        away_elo_post
    FROM nba_games
),
ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY game_date DESC) AS rn
    FROM team_game_union
)
SELECT
    team_id,
    team_name,
    game_date,
    pts,
    opp_pts,
    result,
    ortg,
    drtg,
    elo
FROM ranked
WHERE rn <= 10
ORDER BY team_id, game_date DESC;


-- ── Row Level Security (enable for production) ────────────────
-- Allow public read access to ratings (your app reads these)
ALTER TABLE nba_team_ratings ENABLE ROW LEVEL SECURITY;
ALTER TABLE nba_games        ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read nba_team_ratings"
    ON nba_team_ratings FOR SELECT
    USING (true);

CREATE POLICY "Public read nba_games"
    ON nba_games FOR SELECT
    USING (true);

-- Service role (your scraper) can write
-- This is handled automatically by the service role key in Supabase
