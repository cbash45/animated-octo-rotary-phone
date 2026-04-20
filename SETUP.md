# NBA Analytics Pipeline — Setup Guide

## What this builds
- Scrapes every NBA game from stats.nba.com (free, no API key)
- Calculates ORtg, DRtg, Pace, and Elo from raw box scores
- Stores everything in a free Supabase database
- Runs nightly via GitHub Actions (free)
- Serves ratings to your spread model app via a Netlify edge function

## Architecture
```
stats.nba.com  →  scraper.py  →  Supabase DB  →  /api/ratings  →  Spread Model App
     (free)         (Python)       (free)        (Netlify fn)
```

---

## Step 1 — Set up Supabase (5 minutes)

1. Go to supabase.com → Sign up free
2. Create a new project (name it "nba-analytics")
3. Wait for it to provision (~2 minutes)
4. Go to SQL Editor → paste the contents of `schema.sql` → Run
5. Go to Settings → API → copy two values:
   - **Project URL** (looks like https://xxxx.supabase.co)
   - **service_role key** (the secret one, NOT the anon key)

---

## Step 2 — Run the scraper locally (first time)

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SUPABASE_URL="https://xxxx.supabase.co"
export SUPABASE_KEY="your-service-role-key"

# Run the scraper (takes 2-5 minutes first time)
python scraper.py
```

You should see output like:
```
============================================================
NBA Data Pipeline
Season: 2025-26 | Type: Regular Season
============================================================
Fetching game log for 2025-26 Regular Season...
  Found 2460 team-game records
Fetching advanced team stats...
  Found 30 teams
Processing 1230 games...
  Inserted batch 1
  ...
Calculating rolling team ratings...
Upserting 30 team ratings...

✓ Pipeline complete!
  Games processed: 1230
  Teams rated:     30
```

---

## Step 3 — Verify data in Supabase

1. Go to your Supabase project → Table Editor
2. Open `nba_team_ratings` — you should see 30 rows, one per team
3. Check a team like "Boston Celtics" — verify elo, ortg, drtg look reasonable
   - Elo: should be 1400–1700 range
   - ORtg: should be 108–125 range
   - DRtg: should be 108–120 range

---

## Step 4 — Set up nightly GitHub Actions

1. Push this whole folder to a GitHub repo
2. Go to repo Settings → Secrets and variables → Actions
3. Add two secrets:
   - `SUPABASE_URL` = your Supabase project URL
   - `SUPABASE_KEY` = your service_role key
4. Go to Actions tab → "NBA Nightly Scraper" → Run workflow (test it)
5. It will now run automatically every night at 2am UTC

---

## Step 5 — Add the ratings endpoint to your Netlify spread model

1. Copy `netlify/edge-functions/ratings.js` into your spread model's
   `netlify/edge-functions/` folder
2. Add to your `netlify.toml`:
   ```toml
   [[edge_functions]]
     path = "/api/ratings"
     function = "ratings"
   ```
3. Add environment variables in Netlify dashboard:
   - `SUPABASE_URL` = your Supabase project URL
   - `SUPABASE_KEY` = your **anon key** (public read-only, safe for Netlify)
4. Deploy

---

## Step 6 — Update the spread model app to use live ratings

In `index.html`, change the `fetchStats()` function to call `/api/ratings`
instead of `/api/stats`:

```javascript
async function fetchStats() {
  const home = document.getElementById('homeTeam').value;
  const away = document.getElementById('awayTeam').value;

  const res = await fetch(
    `/api/ratings?home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}`
  );
  const data = await res.json();

  // data already in the right format: { home: {...}, away: {...}, asOf: "..." }
  liveData = data;
  // ... rest of your existing render code
}
```

---

## How the ratings are calculated

### Possessions (per game)
```
possessions = FGA + (0.44 × FTA) - OREB + TOV
```
The 0.44 factor accounts for the "and-one" free throw sequences.

### Offensive Rating (ORtg)
```
ORtg = 100 × points_scored / possessions
```
Points scored per 100 possessions. League average is ~115.

### Defensive Rating (DRtg)
```
DRtg = 100 × points_allowed / possessions
```
Lower is better. League average is ~115.

### Pace
```
Pace = possessions / game_minutes × 48
```
Possessions per 48-minute game. League average is ~99.

### Elo (538 methodology)
- All teams start at 1500
- Updated after every game
- Margin of victory multiplier rewards winning by more
- K-factor of 20 (moderately reactive)
- Expected score formula: 1 / (1 + 10^((opponent_elo - team_elo) / 400))

### Rolling averages
- ORtg, DRtg, Pace are averaged over the last 15 games
- This balances recency (current form) with stability (sample size)
- You can change `window=15` in `build_team_ratings()` to be more/less reactive

---

## Troubleshooting

**"Connection refused" from stats.nba.com**
- The NBA site sometimes rate limits scrapers
- Add `time.sleep(1)` between requests or run at off-peak hours

**Missing games**
- Some preseason or in-season tournament games may have different season codes
- Check the SEASON and SEASON_TYPE constants at the top of scraper.py

**Elo looks wrong**
- First-run Elo always starts at 1500 for all teams
- After a full season of games it will spread out to realistic values (~1400–1650)
- It needs at least 20-30 games per team to stabilize

**Supabase upsert fails**
- Make sure you're using the service_role key (not anon) for writes
- The anon key is read-only by design
