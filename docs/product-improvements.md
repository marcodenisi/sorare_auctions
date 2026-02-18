# Sorare MLS Auction Tracker — Product Improvement Report

**Date:** 2026-02-18
**Contributors:** Product Manager, Software Architect, Software Engineer

---

## Executive Summary

This report presents 14 product improvement ideas for the Sorare MLS Auction Tracker, evaluated for technical feasibility and prioritized by value-to-effort ratio. The tool currently tracks ~100 MLS players' Limited card auction prices via the Sorare GraphQL API, storing history in JSON files and displaying data through a Streamlit dashboard.

**Key findings:**
- 4 quick wins can be implemented with minimal risk, all within `app.py` (charts, statistics, recency indicators, CSV export)
- 1 idea (#7 Automated Fetching) is already implemented via GitHub Actions
- 1 idea (#12 SQLite Migration) is actively counterproductive at current scale
- 2 ideas (#3 Alerts, #11 Auto-Discover) have architectural mismatches that warrant deferral
- Several code quality issues should be addressed alongside feature work (no tests, duplicated code, vestigial CSV generation)

---

## Improvement Ideas

### 1. Interactive Price History Charts
**Category:** UX
**Description:** Replace the flat table of ordinal prices (1st, 2nd, 3rd...) with interactive line charts per player showing price over time. Users can hover for exact values, zoom into date ranges, and overlay multiple players for comparison. Streamlit's built-in Altair integration makes this straightforward.
**Target Benefit:** Visual trend analysis instead of scanning dozens of numeric columns horizontally.

**Feasibility:** Easy | **Architecture Impact:** Minimal | **Effort:** Small | **Risk:** Low
**Implementation:** Add `st.expander()` per player with `st.altair_chart()` inside the tab rendering loop in `app.py`. Data is already date-keyed in JSON history files. Use collapsed-by-default expanders to avoid rendering 100+ charts at once.
**Code changes:** `app.py` only — ~40 lines.

---

### 2. Player Comparison Tool
**Category:** UX / Analytics
**Description:** Add a "Compare" mode where users select 2-4 players and see their price histories overlaid on a single chart, plus a side-by-side stat table (avg price, min, max, volatility, auction count).
**Target Benefit:** Helps managers make informed auction bidding decisions between comparable players.

**Feasibility:** Easy-Medium | **Architecture Impact:** Minimal | **Effort:** Small | **Risk:** Low
**Implementation:** New "Compare" tab in `app.py` with `st.multiselect` for player selection. Overlay lines on a single Altair chart. Requires extracting `_load_player_history()` results into a reusable structure — current `_load_and_prepare()` tightly couples data loading with table formatting.
**Code changes:** `app.py` — ~60-80 lines + minor refactor of data loading.

---

### 3. Price Alerts & Watchlist
**Category:** Notifications
**Description:** Let users set price thresholds (e.g., "alert me if a player drops below $200"). On each fetch run, check thresholds and send notifications.
**Target Benefit:** Timely awareness of buying opportunities without constantly checking the dashboard.

**Feasibility:** Medium-Hard | **Architecture Impact:** Significant | **Effort:** Medium | **Risk:** High
**Implementation:** Requires persistent config (watchlist.yaml), threshold checking in `fetch_auctions.py`, and a notification mechanism. Desktop notifications need `plyer` or similar; email needs SMTP config.
**Key concern:** The fetch script runs in GitHub Actions (headless CI) where desktop notifications are impossible. Email/Slack would require secrets management. Log-file alerting is trivially feasible but low-value. **Significant scope-to-value mismatch for a local tool.**
**Recommendation:** Defer unless Slack/email integration is explicitly desired.

---

### 4. Undervalued Player Detection
**Category:** Analytics
**Description:** Compute a "value score" by comparing a player's recent auction price to their historical average and peer average. Flag players whose price is significantly below expected. Display as a dedicated "Deals" tab or sortable column.
**Target Benefit:** Quickly identify buying opportunities based on statistical deviation.

**Feasibility:** Easy-Medium | **Architecture Impact:** Minimal | **Effort:** Small-Medium | **Risk:** Low-Medium
**Implementation:** Compute `(overall_avg - recent_3_avg) / std_dev` per player. The existing trend indicator (`app.py:64-82`) already does a simpler version (recent 3 vs overall avg with 5% threshold). This enhances that logic.
**Caveat:** With only 20-35 auctions per player from a single season, statistical significance is limited.
**Code changes:** `app.py` — ~25 lines in `_load_and_prepare()`.

---

### 5. Multi-League Expansion (EPL, La Liga, Serie A, Bundesliga)
**Category:** Coverage
**Description:** Generalize `players.yaml` and `fetch_auctions.py` to support multiple leagues. Add a league selector to the dashboard. The Sorare API already supports all leagues via the same `tokenPrices` query.
**Target Benefit:** Broadens the tool's audience from MLS-only to the full Sorare user base.

**Feasibility:** Medium | **Architecture Impact:** Moderate | **Effort:** Medium | **Risk:** Medium
**Implementation:** Restructure to per-league YAML files (`players/{mls,epl,laliga}.yaml`), update fetch script to iterate leagues, add league selector to dashboard. File paths change: `data/history/{league}/{player}.json`.
**Key concern:** Player curation is manual — someone must identify and add player slugs per league. EPL alone could add 200+ players, with fetch time scaling to ~3.5 min per run. The tool's value is in curation, not breadth.
**Code changes:** All 3 files + structural changes.

---

### 6. Multi-Rarity Support (Rare, Super Rare, Unique)
**Category:** Coverage
**Description:** Currently hardcoded to `rarity: limited`. Add a rarity selector to both the fetch script and dashboard. The API already accepts different rarity values.
**Target Benefit:** Track the full card market, not just Limited tier.

**Feasibility:** Easy | **Architecture Impact:** Moderate | **Effort:** Small-Medium | **Risk:** Low
**Implementation:** Parameterize `rarity` in GraphQL queries (`fetch_auctions.py:49`, `backfill.py:55`). Change history file naming to `{slug}_{rarity}.json` or use subdirectories. Add `st.selectbox` rarity selector in `app.py`.
**Caveat:** Rare/Super Rare/Unique have far fewer auctions (often zero for many MLS players). Data will be sparse. GH Actions fetch time roughly doubles if fetching Limited+Rare (~34 batched calls → ~68).
**Code changes:** All 3 files — straightforward parameter changes.

---

### 7. Automated Scheduled Fetching
**Category:** Automation

**Status: ALREADY IMPLEMENTED.** `.github/workflows/fetch-auctions.yml` runs every 30 minutes and auto-commits data changes.
**Minor improvement opportunity:** Add error notification (workflow failure alerts) and retry logic. Consider reducing frequency from 30 min to every 6 hours to reduce commit noise.

---

### 8. Price Volatility & Statistics Panel
**Category:** Analytics
**Description:** Add a statistics section per player showing: standard deviation, coefficient of variation, min/max prices, price range, median, and number of auctions.
**Target Benefit:** Understand price stability for better bidding decisions and risk assessment.

**Feasibility:** Easy | **Architecture Impact:** Minimal | **Effort:** Small | **Risk:** Low
**Implementation:** Compute stats from price lists already loaded by `_load_player_history()`. Display as expandable section or toggle — adding too many columns to the main table would be cluttered. Pairs naturally with #1 (charts): show chart + stats together in an `st.expander`.
**Note:** With ~30 data points, statistical measures have limited precision, but are still informative.
**Code changes:** `app.py` — ~20 lines.

---

### 9. Portfolio Tracker
**Category:** Analytics
**Description:** Let users input their owned cards (player + purchase price) in a YAML file. The dashboard then shows current estimated value, total portfolio value, and unrealized gain/loss per card.
**Target Benefit:** Track investment performance across owned Sorare cards.

**Feasibility:** Medium | **Architecture Impact:** Moderate | **Effort:** Medium | **Risk:** Medium
**Implementation:** New `portfolio.yaml` config file + new dashboard tab (~80-100 lines). Cross-reference portfolio against history data for current valuations. "Current value" is ambiguous — use most recent auction price or average of last 3.
**Caveat:** Streamlit doesn't persist state across sessions, so portfolio config must be file-based. Form-based input in Streamlit is possible but clunky. Direct YAML editing may be more practical. This adds a different user flow (ownership tracking) tangential to the core use case (market intelligence).

---

### 10. Data Quality Dashboard & Gap Detection
**Category:** Data Quality
**Description:** Add a "Data Health" tab showing: players with zero auctions, stale data (no recent auctions), legacy format entries, date gaps, and file/config mismatches.
**Target Benefit:** Confidence in data completeness and accuracy; surfaces fetch issues early.

**Feasibility:** Easy | **Architecture Impact:** Minimal | **Effort:** Small | **Risk:** Low
**Implementation:** New tab in `app.py` that scans history files and reports anomalies. Read `last_updated.txt` for fetch freshness. Flag players with last auction >7 days ago (may indicate trade, injury, or delisting).
**Code changes:** `app.py` — ~40-50 lines.

---

### 11. Auto-Discover New Players
**Category:** Automation
**Description:** Query the Sorare API for active roster players and auto-populate `players.yaml`. Add a `--discover` CLI flag.
**Target Benefit:** Eliminates manual roster management; catches new signings automatically.

**Feasibility:** Medium-Hard | **Architecture Impact:** Moderate | **Effort:** Medium | **Risk:** High
**Implementation:** Requires a different Sorare GraphQL query (`football.allCards` or `football.club.activePlayers`) — schema needs exploration. Team abbreviations and roles aren't available from the API (roles are user-curated projections).
**Key concern:** This would work as a "suggest new players" CLI helper, not full auto-populate. The manual curation is actually a feature — the user tracks specific players they care about. Risk of adding players with zero auction history.
**Recommendation:** Defer unless roster management becomes burdensome.

---

### 12. SQLite Migration for History Storage
**Category:** Performance
**Description:** Replace the 100+ individual JSON files with a single SQLite database.
**Target Benefit:** Better query performance and foundation for advanced analytics.

**Feasibility:** Easy-Medium | **Architecture Impact:** Significant | **Effort:** Medium | **Risk:** Medium-High
**Assessment: NOT RECOMMENDED at current scale.**
- Current data: ~3,093 entries across ~101 files, 404KB total on disk. JSON loads in milliseconds.
- JSON diffs are human-readable and valuable for git-based data versioning. The GH Actions workflow uses `git diff --cached --quiet` to detect changes — this wouldn't work with SQLite binary blobs.
- SQLite adds schema management and migration complexity without solving an actual performance problem.
- Only reconsider if analytics features demand complex queries or data exceeds 10x current volume.

---

### 13. Auction Recency Indicator & "Hot" Players
**Category:** UX
**Description:** Show how recently each player was auctioned (e.g., "2h ago", "3d ago") and highlight "hot" players with high auction frequency.
**Target Benefit:** Understand market activity and liquidity for each player.

**Feasibility:** Easy | **Architecture Impact:** Minimal | **Effort:** Small | **Risk:** Low
**Implementation:** Extract the most recent timestamp from history JSON keys, compute relative time, add as a column. "Hot" players = count of auctions in last N days. Trivially easy — the data already exists.
**Code changes:** `app.py` — ~10 lines.

---

### 14. Export & Sharing Features
**Category:** UX
**Description:** Add export buttons for CSV download of the current filtered view. Could also generate a weekly market report as markdown.
**Target Benefit:** Share insights with friends/league mates; integrate data into external tools.

**Feasibility:** Easy-Medium | **Architecture Impact:** Minimal | **Effort:** Small | **Risk:** Low-Medium
**Implementation:** Use `st.download_button` (built-in) with `df.to_csv()` on the filtered DataFrame. Clipboard copy requires JavaScript injection (fragile). Shareable URLs are impossible with local-only architecture.
**Recommendation:** Implement CSV download only — it's the 80/20 solution.
**Code changes:** `app.py` — ~15 lines per tab.

---

## Priority Matrix

| Priority | Ideas | Rationale |
|----------|-------|-----------|
| **P1 — High Value** | #13 Recency Indicator, #8 Statistics Panel, #1 Price Charts | High user value, low effort, low risk. All `app.py`-only changes |
| **P2 — Medium** | #4 Undervalued Detection, #14 Export (CSV only), #6 Multi-Rarity, #2 Comparison Tool, #10 Data Quality | Medium value, reasonable effort |
| **P3 — Defer** | #5 Multi-League, #9 Portfolio, #3 Alerts, #11 Auto-Discover | High effort, architectural mismatch, or low ROI |
| **Skip** | #7 (already done), #12 (counterproductive) | No action needed |

---

## Recommended Implementation Phases

### Phase 1: Dashboard Enhancements (app.py only, low risk)
| Item | Idea | Effort |
|------|------|--------|
| #13 | Auction Recency Indicator | ~10 lines |
| #8 | Price Volatility & Statistics Panel | ~20 lines |
| #1 | Interactive Price History Charts | ~40 lines |
| #14 | CSV Export (st.download_button) | ~15 lines |

**Rationale:** All changes are in `app.py` only. No backend or data format changes. Immediately visible value. Can be shipped as a single PR.

### Phase 2: Analysis Features (app.py + minor refactor)
| Item | Idea | Effort |
|------|------|--------|
| #4 | Undervalued Player Detection | ~25 lines |
| #2 | Player Comparison Tool (new tab) | ~80 lines |
| — | Refactor: Extract shared `name_from_slug()` into utils module | — |
| — | Remove vestigial CSV generation from `fetch_auctions.py` | — |

**Rationale:** Adds analytical depth. The refactor is necessary to support cross-position comparison. Good time to clean up technical debt.

### Phase 3: Data Expansion (fetch + app changes)
| Item | Idea | Effort |
|------|------|--------|
| #6 | Multi-Rarity Support | All 3 files |
| #10 | Data Quality Dashboard | ~40 lines in app.py |
| — | Add `bcrypt` to `requirements.txt` | 1 line |
| — | Parameterize `SEASON` in `backfill.py` | 1 line |

**Rationale:** Changes the data model. Needs testing with the API to verify rarity data availability. Data quality tab helps monitor the expanded dataset.

### Phase 4: Future / On-Demand
| Item | Idea | Trigger |
|------|------|---------|
| #5 | Multi-League | Only if user wants to track other leagues |
| #9 | Portfolio Tracker | Only if user actively trades cards |
| #3 | Price Alerts | Only if Slack/email integration is desired |
| #11 | Auto-Discover Players | Only as CLI helper, if roster management becomes burdensome |

**Rationale:** These features are either high-effort, tangential to core use case, or blocked by architectural constraints. Implement only when explicitly needed.

---

## Technical Debt to Address

These issues should be resolved alongside feature work, regardless of which phase:

| Issue | Description | Where |
|-------|-------------|-------|
| No tests | Add unit tests for pure functions: `_compute_trend()`, `_make_price_record()`, `name_from_slug()`, `_format_price()` | New test file |
| Duplicated code | `name_from_slug()` exists in both `app.py:85-88` and `fetch_auctions.py:126-132` | Extract to shared module |
| Vestigial CSVs | `fetch_auctions.py` generates CSV files that `app.py` doesn't read (reads JSON instead) | Remove or document |
| Hardcoded season | `SEASON=2026` in `backfill.py:37` should be parameterized | `backfill.py` |
| Unused field | `role` field in `players.yaml` (all "Starter") is never displayed | Either use or remove |
| Missing dependency | `bcrypt` used in `backfill.py` but not listed in `requirements.txt` | `requirements.txt` |
| No failure handling | GH Actions workflow silently fails on API errors | `.github/workflows/` |

---

## Appendix: Architecture Constraints

These constraints informed the feasibility assessments:

- **Storage:** File-based JSON, ~101 files, 404KB total. Git-tracked for version history. No database.
- **UI Framework:** Streamlit — re-executes entire script on every interaction. Limited interactivity compared to React/Vue. No persistent client-side state.
- **API Rate Limits:** Unauthenticated: 20 calls/min, 500 complexity budget. Authenticated: 60 calls/min, 30,000 complexity budget.
- **Deployment:** Local-only. No server, no hosting, no user accounts.
- **Automation:** GitHub Actions cron job every 30 minutes for data refresh.
- **Scale:** ~100 MLS players, ~30 auctions each, single league, single rarity (Limited).
