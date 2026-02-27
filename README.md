# Sorare MLS Auction Tracker

Tracks Limited card auction prices for MLS players on [Sorare](https://sorare.com).
Fetches data from Sorare's GraphQL API, stores historical prices locally, and
displays an interactive Streamlit dashboard.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires Python 3.11+.

## Usage

**Dashboard:**

```bash
streamlit run app.py
```

Opens on `localhost:8501`. Features position-grouped tabs (GK/DF/MF/FW),
currency selector (USD/EUR/GBP/ETH), trend & value indicators, per-player
price charts, cross-position comparison, and CSV export.

**Fetch latest auctions:**

```bash
python fetch_auctions.py
```

Fetches the most recent ~20 auctions per player (unauthenticated).
Also runs automatically every 30 minutes via GitHub Actions.

**Backfill full history (authenticated):**

```bash
python backfill.py
```

Prompts for Sorare credentials, obtains a JWT token, and paginates through
each player's complete auction history using the higher authenticated rate limit.

## Project Structure

```
├── app.py                 # Streamlit dashboard
├── fetch_auctions.py      # Sorare API fetcher (unauthenticated)
├── backfill.py            # Authenticated historical backfill
├── utils.py               # Shared helpers (name_from_slug, ordinal)
├── players.yaml           # Player roster (101 players, 4 positions)
├── requirements.txt
├── data/
│   ├── history/           # Per-player JSON files ({slug}.json)
│   └── last_updated.txt   # Timestamp of last fetch
├── docs/plans/            # Design docs and improvement ideas
└── .github/workflows/     # GitHub Actions cron job
```

### Data storage

Each player has a JSON file in `data/history/` keyed by ISO 8601 timestamp:

```json
{
  "2026-02-18T06:33:02Z": {
    "usd": 126.32,
    "eur": 106.68,
    "gbp": 93.2,
    "eth": 0.0633
  }
}
```

## Sorare API Integration

All queries go to `https://api.sorare.com/graphql`.

### Token prices query

Used by both `fetch_auctions.py` and `backfill.py` to retrieve auction history:

```graphql
query GetLimitedAuctionHistory(
  $playerSlug: String!
  $first: Int
  $to: ISO8601DateTime
) {
  tokens {
    tokenPrices(
      playerSlug: $playerSlug
      rarity: limited
      first: $first
      to: $to
    ) {
      amounts { usdCents, eurCents, gbpCents, wei }
      date
      deal { ... on TokenAuction { id } }
    }
  }
}
```

- `first` controls page size (default 20)
- `to` is used for date-windowing pagination: the oldest `date` from one batch
  becomes the `to` parameter for the next
- The API returns multiple deal types; only entries where
  `deal { ... on TokenAuction { id } }` resolves are actual auctions
- `backfill.py` adds a `season: $season` filter (hardcoded to 2026)

### Batched queries

`fetch_auctions.py` batches 3 players per API call using GraphQL aliases:

```graphql
query {
  tokens {
    player0: tokenPrices(playerSlug: "slug-a", rarity: limited, first: 20) { ... }
    player1: tokenPrices(playerSlug: "slug-b", rarity: limited, first: 20) { ... }
    player2: tokenPrices(playerSlug: "slug-c", rarity: limited, first: 20) { ... }
  }
}
```

### Authentication (backfill only)

`backfill.py` authenticates to get higher rate limits:

1. `GET https://api.sorare.com/api/v1/users/{email}` to fetch bcrypt salt
2. Hash password with salt using bcrypt
3. `signIn` GraphQL mutation to obtain a JWT token (handles 2FA if enabled)
4. Subsequent requests include `Authorization: Bearer {token}` and `JWT-AUD` headers

### Rate limits

| Mode             | Calls/min | Complexity budget | Sleep between calls |
|------------------|-----------|-------------------|---------------------|
| Unauthenticated  | 20        | 500               | 3s                  |
| Authenticated    | 60        | 30,000            | 1s                  |

When a complexity error is returned, the fetcher stops gracefully (partial
history is acceptable and will be completed on subsequent runs).

## Automation

GitHub Actions (`.github/workflows/fetch-auctions.yml`) runs
`fetch_auctions.py` every 30 minutes and auto-commits any data changes.
