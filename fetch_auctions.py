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

Multi-currency support: stores USD, EUR, GBP, and ETH prices in
each history entry.  CSV files use USD for backward compatibility.

Backfill mode (--backfill): paginates backward through each player's
full auction history using date-windowing.
"""

import argparse
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
BACKFILL_SLEEP_SECONDS = 5  # longer sleep in backfill mode

WEI_PER_ETH = 10**18

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
        eurCents
        gbpCents
        wei
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
        eurCents
        gbpCents
        wei
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
# Multi-currency price record helpers
# ---------------------------------------------------------------------------

def _make_price_record(amounts: dict) -> dict:
    """Build a multi-currency price dict from an API 'amounts' object.

    Returns: {"usd": float, "eur": float, "gbp": float, "eth": float}
    """
    usd_cents = amounts.get("usdCents")
    eur_cents = amounts.get("eurCents")
    gbp_cents = amounts.get("gbpCents")
    wei = amounts.get("wei")

    return {
        "usd": usd_cents / 100.0 if usd_cents is not None else None,
        "eur": eur_cents / 100.0 if eur_cents is not None else None,
        "gbp": gbp_cents / 100.0 if gbp_cents is not None else None,
        "eth": int(wei) / WEI_PER_ETH if wei is not None else None,
    }


def _migrate_old_entry(value) -> dict:
    """Migrate a legacy history entry (bare float) to multi-currency format.

    Old format: {date: 124.65}  (USD only)
    New format: {date: {"usd": 124.65, "eur": null, "gbp": null, "eth": null}}
    """
    if isinstance(value, dict):
        return value  # already new format
    # Bare float/int -> treat as USD
    return {"usd": float(value), "eur": None, "gbp": None, "eth": None}


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


def _parse_token_prices(token_prices: list[dict]) -> tuple[list[tuple[str, dict]], int, str | None]:
    """Extract (date, price_record) tuples from a tokenPrices response list.

    Only keeps TokenAuction deals (where deal.id is present).
    Each price_record is {"usd": ..., "eur": ..., "gbp": ..., "eth": ...}.

    Returns (auctions, raw_count, oldest_date) where raw_count is the total
    number of entries before filtering (needed for pagination decisions).
    """
    results: list[tuple[str, dict]] = []
    oldest_date: str | None = None
    for tp in token_prices:
        deal = tp.get("deal")
        if deal and deal.get("id"):
            price_record = _make_price_record(tp["amounts"])
            date = tp.get("date", "")
            results.append((date, price_record))
        d = tp.get("date")
        if d and (oldest_date is None or d < oldest_date):
            oldest_date = d
    return results, len(token_prices), oldest_date


def fetch_batch_auction_prices(slugs: list[str]) -> dict[str, tuple[list[tuple[str, dict]], int, str | None] | None]:
    """Fetch auction prices for multiple players in a single batched API call.

    Returns a dict mapping slug -> (auctions, raw_count, oldest_date) or None.
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

    results = {}
    for i, slug in enumerate(slugs):
        alias = f"player{i}"
        token_prices = tokens.get(alias) or []
        results[slug] = _parse_token_prices(token_prices)

    return results


def load_history(path: str) -> dict[str, dict]:
    """Load previously saved auction history from JSON.

    Handles backward compatibility: migrates old format {date: float}
    to new format {date: {"usd": ..., "eur": ..., "gbp": ..., "eth": ...}}.
    """
    if os.path.isfile(path):
        with open(path, "r") as f:
            raw = json.load(f)
        # Migrate each entry
        return {date: _migrate_old_entry(val) for date, val in raw.items()}
    return {}


def save_history(path: str, history: dict[str, dict]) -> None:
    """Save auction history to JSON."""
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


def fetch_auction_prices(slug: str, paginate: bool = False, sleep_seconds: int = SLEEP_SECONDS) -> list[tuple[str, dict]]:
    """
    Return a list of (date, price_record) tuples for a player's auctions,
    ordered most-recent-first.

    If paginate=False (default), fetches only the first page (most recent
    BATCH_SIZE results).  If paginate=True, uses date-windowing to fetch
    all available history.

    Uses date-windowing pagination: fetch up to BATCH_SIZE results,
    take the oldest date, use it as ``to`` for the next call.  Stop when
    the batch returns empty, fewer results than BATCH_SIZE, or the API
    rejects the query due to complexity limits (unauthenticated access).
    """
    results: list[tuple[str, dict]] = []
    to_cursor: str | None = None
    prev_cursor: str | None = None
    first_request = True
    page = 0

    while True:
        variables: dict = {"playerSlug": slug, "first": BATCH_SIZE}
        if to_cursor is not None:
            variables["to"] = to_cursor

        # Rate-limit: sleep before every request except the very first
        # one for this player.
        if not first_request:
            time.sleep(sleep_seconds)
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
                price_record = _make_price_record(tp["amounts"])
                date = tp.get("date", "")
                results.append((date, price_record))

            # Track oldest date for pagination cursor
            d = tp.get("date")
            if d:
                if oldest_date is None or d < oldest_date:
                    oldest_date = d

        # Stop if batch was smaller than requested (end of data)
        if len(token_prices) < BATCH_SIZE:
            break

        # Only paginate if explicitly requested (backfill mode)
        if not paginate:
            break

        # Use the oldest date as the upper-bound for the next page
        if oldest_date is None:
            break
        if oldest_date == prev_cursor:
            break
        prev_cursor = to_cursor
        to_cursor = oldest_date
        page += 1

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
    new_auctions: list[tuple[str, dict]],
    history_dir: str,
) -> tuple[str, str, list[float]]:
    """Merge new auctions into history, save, and return a row tuple.

    Returns (display_name, team, [usd_prices_sorted_by_date]) for CSV output.
    """
    history_path = os.path.join(history_dir, f"{slug}.json")
    history = load_history(history_path)

    new_count = 0
    for date, price_record in new_auctions:
        if date not in history:
            new_count += 1
        history[date] = price_record

    save_history(history_path, history)

    # For CSV backward compatibility, extract USD prices sorted by date
    sorted_usd_prices = []
    for _, price_rec in sorted(history.items()):
        usd = price_rec.get("usd")
        if usd is not None:
            sorted_usd_prices.append(usd)

    print(f"{len(sorted_usd_prices)} total auctions ({new_count} new)")
    return (name_from_slug(slug), team, sorted_usd_prices)


def _backfill_player(
    slug: str,
    team: str,
    initial_auctions: list[tuple[str, dict]],
    raw_count: int,
    initial_oldest_date: str | None,
    history_dir: str,
) -> tuple[str, str, list[float]]:
    """Backfill a single player's full auction history.

    After the initial fetch, paginates backward using the oldest date
    from each batch as the 'to' parameter.

    raw_count is the total API results before auction filtering (used to
    decide if there are more pages — the API returns up to BATCH_SIZE
    results including non-auction deals).

    If the API returns a complexity error, waits 5 seconds and retries
    the same page as an individual (non-batched) query.

    Returns the same tuple format as _process_player_results.
    """
    history_path = os.path.join(history_dir, f"{slug}.json")
    history = load_history(history_path)

    new_count = 0
    for date, price_record in initial_auctions:
        if date not in history:
            new_count += 1
        history[date] = price_record

    # If the raw API response had fewer than BATCH_SIZE, no more pages
    if raw_count < BATCH_SIZE:
        save_history(history_path, history)
        sorted_usd_prices = [
            rec.get("usd") for _, rec in sorted(history.items())
            if rec.get("usd") is not None
        ]
        print(f"{len(sorted_usd_prices)} total auctions ({new_count} new, no further pages)")
        return (name_from_slug(slug), team, sorted_usd_prices)

    oldest_date = initial_oldest_date

    if oldest_date is None:
        save_history(history_path, history)
        sorted_usd_prices = [
            rec.get("usd") for _, rec in sorted(history.items())
            if rec.get("usd") is not None
        ]
        print(f"{len(sorted_usd_prices)} total auctions ({new_count} new)")
        return (name_from_slug(slug), team, sorted_usd_prices)

    # Paginate backward
    to_cursor = oldest_date
    prev_cursor = None
    page = 2  # we already have page 1

    while True:
        time.sleep(BACKFILL_SLEEP_SECONDS)

        variables: dict = {"playerSlug": slug, "first": BATCH_SIZE, "to": to_cursor}
        resp = requests.post(
            API_URL,
            json={"query": QUERY, "variables": variables},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()

        if _has_complexity_error(body):
            # Retry after waiting -- individual query with 'to' may also fail
            print(f"\n    Complexity error on page {page}, waiting 5s and retrying...", end=" ", flush=True)
            time.sleep(5)
            resp = requests.post(
                API_URL,
                json={"query": QUERY, "variables": variables},
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()

            if _has_complexity_error(body):
                print(f"\n    Complexity error persists. Backfill requires an API key for deeper history.")
                break

        # Surface non-complexity errors
        for err in body.get("errors", []):
            print(f"\n    API error: {err.get('message', err)}", end=" ")

        data = body.get("data") or {}
        tokens = data.get("tokens") or {}
        token_prices = tokens.get("tokenPrices") or []
        if not token_prices:
            break

        page_new = 0
        page_oldest = None
        for tp in token_prices:
            deal = tp.get("deal")
            if deal and deal.get("id"):
                price_record = _make_price_record(tp["amounts"])
                date = tp.get("date", "")
                is_new = date not in history
                if is_new:
                    page_new += 1
                    new_count += 1
                history[date] = price_record

            d = tp.get("date")
            if d:
                if page_oldest is None or d < page_oldest:
                    page_oldest = d

        print(f"  Backfilling {slug}... page {page} ({page_new} more auctions)", flush=True)

        if len(token_prices) < BATCH_SIZE:
            break

        if page_oldest is None:
            break
        if page_oldest == prev_cursor:
            break
        prev_cursor = to_cursor
        to_cursor = page_oldest
        page += 1

    save_history(history_path, history)
    sorted_usd_prices = [
        rec.get("usd") for _, rec in sorted(history.items())
        if rec.get("usd") is not None
    ]
    print(f"  {slug}: {len(sorted_usd_prices)} total auctions after backfill")
    return (name_from_slug(slug), team, sorted_usd_prices)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Sorare auction prices")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Paginate backward through full auction history for each player",
    )
    args = parser.parse_args()

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

        # Collect rows: each row is (display_name, team, [usd_prices])
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
                # fetch_auction_prices handles its own pagination when paginate=True
                for p in batch:
                    slug = p["slug"]
                    team = p["team"]
                    time.sleep(SLEEP_SECONDS)
                    print(f"  [fallback] Fetching {slug}...", end=" ", flush=True)
                    new_auctions = fetch_auction_prices(slug, paginate=args.backfill,
                                                        sleep_seconds=BACKFILL_SLEEP_SECONDS if args.backfill else SLEEP_SECONDS)
                    api_calls += 1
                    rows.append(_process_player_results(slug, team, new_auctions, history_dir))
            else:
                # Process batched results
                for p in batch:
                    slug = p["slug"]
                    team = p["team"]
                    print(f"  {slug}...", end=" ", flush=True)
                    auctions, raw_count, oldest_date = batch_results[slug]
                    if args.backfill:
                        rows.append(_backfill_player(slug, team, auctions, raw_count, oldest_date, history_dir))
                    else:
                        rows.append(_process_player_results(slug, team, auctions, history_dir))

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
