# Sorare MLS Limited Auction Tracker

## Goal

Track historical auction prices for a curated set of MLS players' Limited cards on Sorare. Display the data in a local web dashboard.

## Architecture

Two independent components with CSV files as the interface:

```
players.yaml --> fetch_auctions.py --> limited_*.csv --> app.py (Streamlit)
```

No database. No server. Fully local.

## Components

### 1. Player Config (`players.yaml`)

A manually maintained YAML file listing players to track, grouped by position.

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

Fields:
- `slug`: Sorare player slug (used in API queries)
- `team`: MLS team abbreviation (display only)
- `role`: Projected role — one of: Starter, Likely Starter, Lean Starter, Lean Backup, Likely Backup, Backup

### 2. Fetch Script (`fetch_auctions.py`)

A CLI script that:

1. Reads `players.yaml`
2. For each player, queries the Sorare GraphQL API (`https://api.sorare.com/federation/graphql`) for all completed Limited card English auctions
3. Extracts the final price in ETH and converts to USD using the exchange rate at time of fetch
4. Writes one CSV per position: `limited_gk.csv`, `limited_df.csv`, `limited_mf.csv`, `limited_fw.csv`

CSV format:
```
player,team,role,avg_price,1st,2nd,3rd,...,Nth
"Celentano (CIN)",CIN,Starter,370.78,388.79,379.26,344.30,...
```

- All auctions are included (no cap), most recent first
- `avg_price` is the mean of all auction prices
- Prices are in USD, rounded to 2 decimal places

### 3. Dashboard (`app.py`)

A Streamlit app that reads the CSV files and displays a tabbed dashboard.

Layout per tab:
| Player | Proj Role | Trend | Avg Price | 1st | 2nd | 3rd | ... |
|--------|-----------|-------|-----------|-----|-----|-----|-----|

- **4 tabs**: LimitedGK, LimitedDF, LimitedMF, LimitedFW
- **Sorted** by average price descending
- **Trend**: compare average of 3 most recent auctions to overall average. Green arrow up if >5% higher, red arrow down if >5% lower, gray flat otherwise
- **Prices**: formatted as `$370.78`
- **Scrollable** horizontally for players with many auctions
- Auto-reloads when CSV files change on disk

### Dependencies

- `requests` — HTTP calls to Sorare API
- `pyyaml` — parse player config
- `streamlit` — dashboard
- `pandas` — data handling

### Usage

```bash
# Fetch latest auction data
python fetch_auctions.py

# Start dashboard
streamlit run app.py
```
