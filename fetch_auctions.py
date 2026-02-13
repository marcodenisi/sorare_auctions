"""
Fetch Sorare Limited auction prices for players listed in players.yaml.

Queries the Sorare GraphQL API (unauthenticated) for each player's
TokenAuction history on Limited cards, then writes one CSV per position
group into the data/ directory.

Uses batched GraphQL queries with aliases to fetch multiple players per
API call, reducing total requests by ~3x.

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
BATCH_SIZE = 20          # max results per player per API call
PLAYERS_PER_BATCH = 3    # players per batched GraphQL request (conservative)
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

# Fragment for tokenPrices fields, reused in batched queries
TOKEN_PRICES_FIELDS = """
      amounts {
        usdCents
      }
      date
      deal {
        ... on TokenAuction {
          id
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


def build_batch_query(slugs: list[str]) -> str:
    """Build a single GraphQL query that fetches tokenPrices for multiple players
    using aliases (player0, player1, ...).

    Example output:
        query {
          tokens {
            player0: tokenPrices(playerSlug: "slug-a", rarity: limited, first: 20) { ... }
            player1: tokenPrices(playerSlug: "slug-b", rarity: limited, first: 20) { ... }
          }
        }
    """
    alias_parts = []
    for i, slug in enumerate(slugs):
        alias_parts.append(
            f'    player{i}: tokenPrices(playerSlug: "{slug}", rarity: limited, first: {BATCH_SIZE}) {{{TOKEN_PRICES_FIELDS}    }}'
        )
    body = "\n".join(alias_parts)
    return f"query {{\n  tokens {{\n{body}\n  }}\n}}"


def _parse_token_prices(token_prices: list[dict]) -> list[tuple[str, float]]:
    """Extract (date, price_usd) tuples from a tokenPrices response list.

    Only keeps TokenAuction deals (where deal.id is present).
    """
    results: list[tuple[str, float]] = []
    for tp in token_prices:
        deal = tp.get("deal")
        if deal and deal.get("id"):
            usd_cents = tp["amounts"]["usdCents"]
            date = tp.get("date", "")
            results.append((date, usd_cents / 100.0))
    return results


def fetch_batch_auction_prices(slugs: list[str]) -> dict[str, list[tuple[str, float]] | None]:
    """Fetch auction prices for multiple players in a single batched API call.

    Returns a dict mapping slug -> list of (date, price_usd) tuples.
    If the batch fails due to complexity, returns None for all slugs
    (signalling the caller should fall back to individual queries).
    """
    query = build_batch_query(slugs)
    resp = requests.post(
        API_URL,
        json={"query": query},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()

    # If complexity error, signal fallback
    if _has_complexity_error(body):
        print("\n  [batch] Complexity error, falling back to individual queries")
        return {slug: None for slug in slugs}

    # Surface non-complexity errors
    for err in body.get("errors", []):
        print(f"\n  API error: {err.get('message', err)}", end=" ")

    data = body.get("data") or {}
    tokens = data.get("tokens") or {}

    results: dict[str, list[tuple[str, float]]] = {}
    for i, slug in enumerate(slugs):
        alias = f"player{i}"
        token_prices = tokens.get(alias) or []
        results[slug] = _parse_token_prices(token_prices)

    return results


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

def _process_player_results(
    slug: str,
    team: str,
    new_auctions: list[tuple[str, float]],
    history_dir: str,
) -> tuple[str, str, list[float]]:
    """Merge new auctions into history, save, and return a row tuple."""
    history_path = os.path.join(history_dir, f"{slug}.json")
    history = load_history(history_path)

    new_count = 0
    for date, price in new_auctions:
        if date not in history:
            new_count += 1
        history[date] = price

    save_history(history_path, history)

    sorted_prices = [price for _, price in sorted(history.items())]
    print(f"{len(sorted_prices)} total auctions ({new_count} new)")
    return (name_from_slug(slug), team, sorted_prices)


def main() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    players_path = os.path.join(base_dir, "players.yaml")
    data_dir = os.path.join(base_dir, "data")
    history_dir = os.path.join(data_dir, "history")
    os.makedirs(history_dir, exist_ok=True)

    with open(players_path, "r") as f:
        players_data = yaml.safe_load(f)

    start_time = time.time()
    api_calls = 0
    first_request = True

    for pos in POSITIONS:
        players = players_data.get(pos, [])
        if not players:
            continue

        # Collect rows: each row is (display_name, team, [prices])
        rows: list[tuple[str, str, list[float]]] = []

        # Process players in batches of PLAYERS_PER_BATCH
        for batch_start in range(0, len(players), PLAYERS_PER_BATCH):
            batch = players[batch_start:batch_start + PLAYERS_PER_BATCH]
            batch_slugs = [p["slug"] for p in batch]

            # Sleep between API calls (not between individual players)
            if not first_request:
                time.sleep(SLEEP_SECONDS)
            first_request = False

            print(f"Fetching batch [{', '.join(batch_slugs)}]...", flush=True)

            # Try batched query first
            batch_results = fetch_batch_auction_prices(batch_slugs)
            api_calls += 1

            # Check if batch failed (complexity error) -- fall back to individual
            needs_fallback = any(v is None for v in batch_results.values())

            if needs_fallback:
                # Fall back to individual queries for this batch
                for p in batch:
                    slug = p["slug"]
                    team = p["team"]
                    time.sleep(SLEEP_SECONDS)
                    print(f"  [fallback] Fetching {slug}...", end=" ", flush=True)
                    new_auctions = fetch_auction_prices(slug)
                    api_calls += 1
                    rows.append(_process_player_results(slug, team, new_auctions, history_dir))
            else:
                # Process batched results
                for p in batch:
                    slug = p["slug"]
                    team = p["team"]
                    print(f"  {slug}...", end=" ", flush=True)
                    new_auctions = batch_results[slug]
                    rows.append(_process_player_results(slug, team, new_auctions, history_dir))

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

    elapsed = time.time() - start_time
    print(f"\nDone in {elapsed:.1f}s with {api_calls} API calls")

    # Write last-updated timestamp
    ts_path = os.path.join(data_dir, "last_updated.txt")
    with open(ts_path, "w") as f:
        f.write(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


if __name__ == "__main__":
    main()
