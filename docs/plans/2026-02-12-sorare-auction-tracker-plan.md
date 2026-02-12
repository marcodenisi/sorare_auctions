# Sorare MLS Limited Auction Tracker — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local tool to fetch Sorare Limited card auction prices for curated MLS players and display them in a Streamlit dashboard.

**Architecture:** A fetch script reads `players.yaml`, queries the Sorare GraphQL API for all completed Limited English auctions per player, writes CSV files (one per position). A separate Streamlit app reads the CSVs and renders a tabbed dashboard.

**Tech Stack:** Python 3, requests, pyyaml, pandas, streamlit

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`

**Step 1: Create requirements.txt**

```
requests
pyyaml
pandas
streamlit
```

**Step 2: Create .gitignore**

```
__pycache__/
*.pyc
.venv/
data/
```

**Step 3: Create virtual environment and install dependencies**

Run:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Step 4: Create data directory**

Run:
```bash
mkdir -p data
```

**Step 5: Commit**

```bash
git add requirements.txt .gitignore
git commit -m "chore: add project setup (requirements, gitignore)"
```

---

## Task 2: Player Config

**Files:**
- Create: `players.yaml`

**Step 1: Create players.yaml with a few sample MLS players**

```yaml
gk:
  - slug: "roman-celentano"
    team: "CIN"
    role: "Starter"

df:
  - slug: "miles-robinson"
    team: "CIN"
    role: "Starter"

mf:
  - slug: "luciano-acosta"
    team: "CIN"
    role: "Starter"

fw:
  - slug: "denis-bouanga"
    team: "LAFC"
    role: "Starter"
```

**Step 2: Commit**

```bash
git add players.yaml
git commit -m "chore: add sample player config"
```

---

## Task 3: Fetch Script — Core API Client

**Files:**
- Create: `fetch_auctions.py`

**Step 1: Write the fetch script**

The script must:

1. Read `players.yaml`
2. For each player, query `https://api.sorare.com/graphql` using the `tokens.tokenPrices` query:

```graphql
query GetLimitedAuctionHistory($playerSlug: String!, $first: Int, $to: ISO8601DateTime) {
  tokens {
    tokenPrices(
      playerSlug: $playerSlug
      rarity: limited
      first: $first
      to: $to
    ) {
      amounts {
        usdCents
      }
      date
      deal {
        ... on TokenAuction {
          id
        }
      }
    }
  }
}
```

Key implementation details:

- **Pagination**: `tokenPrices` returns a flat array, not a connection. Use date windowing: fetch a batch (e.g., `first: 50`), note the oldest `date`, then use that as `to` for the next batch. Stop when batch returns empty or fewer results than `first`.
- **Filter English Auctions only**: Check that `deal` resolves to a `TokenAuction` (not `TokenOffer`). Auction deal IDs are not null when the fragment matches.
- **Price**: Use `amounts.usdCents` directly (integer cents). Divide by 100 for dollar display. No ETH conversion needed.
- **Rate limiting**: Unauthenticated limit is 20 calls/minute. Add a small delay between requests (e.g., `time.sleep(3)`) to stay safe.
- **Output**: Write one CSV per position into `data/` directory:
  - `data/limited_gk.csv`
  - `data/limited_df.csv`
  - `data/limited_mf.csv`
  - `data/limited_fw.csv`

CSV format:
```
player,team,role,1st,2nd,3rd,...,Nth
"Celentano (CIN)",CIN,Starter,388.79,379.26,344.30,...
```

- Columns `1st`, `2nd`, etc. are ordered most-recent-first
- Prices are USD formatted to 2 decimal places
- Players with zero auctions still appear with empty price columns
- Print progress to stdout as it fetches (e.g., `Fetching roman-celentano... 12 auctions found`)

**Step 2: Run the script to verify it works**

Run:
```bash
source .venv/bin/activate
python fetch_auctions.py
```

Expected: CSVs appear in `data/`, with auction prices for the sample players.

**Step 3: Spot-check output**

Run:
```bash
head -5 data/limited_gk.csv
```

Expected: Header row + player rows with dollar amounts.

**Step 4: Commit**

```bash
git add fetch_auctions.py
git commit -m "feat: add fetch script for Sorare Limited auction prices"
```

---

## Task 4: Streamlit Dashboard

**Files:**
- Create: `app.py`

**Step 1: Write the Streamlit dashboard**

The app must:

1. Read CSVs from `data/limited_gk.csv`, `data/limited_df.csv`, `data/limited_mf.csv`, `data/limited_fw.csv`
2. Display 4 tabs: LimitedGK, LimitedDF, LimitedMF, LimitedFW
3. For each tab, display a table with columns:
   - **Player**: from CSV `player` column (e.g., "Celentano (CIN)")
   - **Proj Role**: from CSV `role` column
   - **Trend**: computed — compare average of 3 most recent auctions to overall average. Show "↑" (green) if >5% higher, "↓" (red) if >5% lower, "→" (gray) otherwise. If fewer than 4 auctions, show "—".
   - **Avg Price**: computed — mean of all auction prices, formatted as `$370.78`
   - **1st, 2nd, 3rd, ...**: auction prices formatted as `$388.79`, empty string if no data
4. Sort rows by average price descending
5. Use `st.dataframe` for a scrollable, sortable table
6. Page title: "Sorare MLS Limited Auctions"
7. Handle missing CSV files gracefully (show "No data. Run fetch_auctions.py first.")

**Step 2: Run and verify**

Run:
```bash
source .venv/bin/activate
streamlit run app.py
```

Expected: Dashboard opens in browser with 4 tabs, populated from the CSVs generated in Task 3.

**Step 3: Commit**

```bash
git add app.py
git commit -m "feat: add Streamlit dashboard for auction data"
```

---

## Task 5: Manual Verification & Adjustments

**Step 1: Run full end-to-end**

```bash
source .venv/bin/activate
python fetch_auctions.py
streamlit run app.py
```

**Step 2: Verify dashboard matches expected layout**

Check:
- [ ] 4 tabs render correctly
- [ ] Prices show as `$XXX.XX`
- [ ] Trend arrows display with correct colors
- [ ] Table is sorted by avg price descending
- [ ] Horizontal scroll works for many columns
- [ ] Players with few/no auctions display correctly

**Step 3: Fix any issues found, commit**

```bash
git add -A
git commit -m "fix: dashboard adjustments after manual testing"
```
