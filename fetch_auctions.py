"""
Fetch Sorare Limited auction prices for players listed in players.yaml.

Queries the Sorare GraphQL API (unauthenticated) for each player's
TokenAuction history on Limited cards, then writes one CSV per position
group into the data/ directory.

Results are persisted in JSON files (data/history/*.json) so that
repeated runs accumulate full history despite the API's per-request
limit of ~20 results.
"""

import csv
import json
import os
import re
import time
from datetime import datetime, timezone

import requests
import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_URL = "https://api.sorare.com/graphql"
BATCH_SIZE = 20          # max allowed by the API
SLEEP_SECONDS = 3        # stay within 20 calls/min unauthenticated limit

QUERY = """
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
"""

POSITIONS = ["gk", "df", "mf", "fw"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def name_from_slug(slug: str) -> str:
    """Derive a display name from a slug like 'roman-celentano' -> 'Roman Celentano'.

    Strips trailing date suffixes used for disambiguation (e.g. '-1998-09-01').
    """
    cleaned = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", slug)
    return " ".join(part.capitalize() for part in cleaned.split("-"))


def _has_complexity_error(body: dict) -> bool:
    """Return True if the API response contains a query-complexity error."""
    for err in body.get("errors", []):
        msg = err.get("message", "")
        if "complexity" in msg.lower():
            return True
    return False


def load_history(path: str) -> dict[str, float]:
    """Load previously saved auction history {date: price} from JSON."""
    if os.path.isfile(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_history(path: str, history: dict[str, float]) -> None:
    """Save auction history {date: price} to JSON."""
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


def fetch_auction_prices(slug: str) -> list[tuple[str, float]]:
    """
    Return a list of (date, price_usd) tuples for a player's auctions,
    ordered most-recent-first.

    Uses date-windowing pagination:  fetch up to BATCH_SIZE results,
    take the oldest date, use it as ``to`` for the next call.  Stop when
    the batch returns empty, fewer results than BATCH_SIZE, or the API
    rejects the query due to complexity limits (unauthenticated access).
    """
    results: list[tuple[str, float]] = []
    to_cursor: str | None = None
    prev_cursor: str | None = None
    first_request = True

    while True:
        variables: dict = {"playerSlug": slug, "first": BATCH_SIZE}
        if to_cursor is not None:
            variables["to"] = to_cursor

        # Rate-limit: sleep before every request except the very first
        # one for this player.
        if not first_request:
            time.sleep(SLEEP_SECONDS)
        first_request = False

        resp = requests.post(
            API_URL,
            json={"query": QUERY, "variables": variables},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()

        # Complexity / other hard errors -- stop pagination gracefully.
        if _has_complexity_error(body):
            break

        # Surface non-complexity errors (e.g. "player not found")
        for err in body.get("errors", []):
            print(f"\n  API error: {err.get('message', err)}", end=" ")

        data = body.get("data") or {}
        tokens = data.get("tokens") or {}
        token_prices = tokens.get("tokenPrices") or []
        if not token_prices:
            break

        oldest_date = None
        for tp in token_prices:
            # Only keep TokenAuction deals (deal.id is present)
            deal = tp.get("deal")
            if deal and deal.get("id"):
                usd_cents = tp["amounts"]["usdCents"]
                date = tp.get("date", "")
                results.append((date, usd_cents / 100.0))

            # Track oldest date for pagination cursor
            d = tp.get("date")
            if d:
                if oldest_date is None or d < oldest_date:
                    oldest_date = d

        # Stop if batch was smaller than requested (end of data)
        if len(token_prices) < BATCH_SIZE:
            break

        # Use the oldest date as the upper-bound for the next page
        if oldest_date is None:
            break
        if oldest_date == prev_cursor:
            break
        prev_cursor = to_cursor
        to_cursor = oldest_date

    return results


def ordinal(n: int) -> str:
    """Return ordinal string for a 1-based index: 1 -> '1st', 2 -> '2nd', ..."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    players_path = os.path.join(base_dir, "players.yaml")
    data_dir = os.path.join(base_dir, "data")
    history_dir = os.path.join(data_dir, "history")
    os.makedirs(history_dir, exist_ok=True)

    with open(players_path, "r") as f:
        players_data = yaml.safe_load(f)

    for pos in POSITIONS:
        players = players_data.get(pos, [])
        if not players:
            continue

        # Collect rows: each row is (display_name, team, [prices])
        rows: list[tuple[str, str, list[float]]] = []

        for i, p in enumerate(players):
            slug = p["slug"]
            team = p["team"]

            # Sleep between players to respect rate limit
            if not (pos == POSITIONS[0] and i == 0):
                time.sleep(SLEEP_SECONDS)

            # Load existing history for this player
            history_path = os.path.join(history_dir, f"{slug}.json")
            history = load_history(history_path)

            print(f"Fetching {slug}...", end=" ", flush=True)
            new_auctions = fetch_auction_prices(slug)

            # Merge new auctions into history (date is the key)
            new_count = 0
            for date, price in new_auctions:
                if date not in history:
                    new_count += 1
                history[date] = price

            save_history(history_path, history)

            # Sort by date ascending (oldest first) and extract prices
            sorted_prices = [
                price for _, price in sorted(history.items())
            ]

            print(f"{len(sorted_prices)} total auctions ({new_count} new)")
            rows.append((name_from_slug(slug), team, sorted_prices))

        # Determine max number of price columns across all players in group
        max_prices = max((len(r[2]) for r in rows), default=0)

        # Build header
        header = ["player", "team"]
        header += [ordinal(n) for n in range(1, max_prices + 1)]

        csv_path = os.path.join(data_dir, f"limited_{pos}.csv")
        with open(csv_path, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(header)
            for name, team, prices in rows:
                price_strs = [f"{p:.2f}" for p in prices]
                # Pad with empty strings if this player has fewer prices
                price_strs += [""] * (max_prices - len(price_strs))
                writer.writerow([name, team] + price_strs)

        print(f"Wrote {csv_path}")

    # Write last-updated timestamp
    ts_path = os.path.join(data_dir, "last_updated.txt")
    with open(ts_path, "w") as f:
        f.write(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


if __name__ == "__main__":
    main()
