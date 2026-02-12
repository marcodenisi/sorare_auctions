"""
Fetch Sorare Limited auction prices for players listed in players.yaml.

Queries the Sorare GraphQL API (unauthenticated) for each player's
TokenAuction history on Limited cards, then writes one CSV per position
group into the data/ directory.

Note: Unauthenticated API access has a query-complexity budget of 500.
Using the ``to`` date-windowing parameter pushes complexity above this
limit, so pagination is only possible with an API key.  Without one the
script fetches a single batch of up to BATCH_SIZE most-recent prices per
player.
"""

import csv
import os
import time

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

def last_name_from_slug(slug: str) -> str:
    """Extract capitalised last name from a slug like 'roman-celentano'."""
    return slug.split("-")[-1].capitalize()


def display_name(slug: str, team: str) -> str:
    """Build the display name: 'Celentano (CIN)'."""
    return f"{last_name_from_slug(slug)} ({team})"


def _has_complexity_error(body: dict) -> bool:
    """Return True if the API response contains a query-complexity error."""
    for err in body.get("errors", []):
        msg = err.get("message", "")
        if "complexity" in msg.lower():
            return True
    return False


def fetch_auction_prices(slug: str) -> list[float]:
    """
    Return a list of auction prices (USD dollars, float) for a player,
    ordered most-recent-first.

    Uses date-windowing pagination:  fetch up to BATCH_SIZE results,
    take the oldest date, use it as ``to`` for the next call.  Stop when
    the batch returns empty, fewer results than BATCH_SIZE, or the API
    rejects the query due to complexity limits (unauthenticated access).
    """
    all_prices: list[float] = []
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
                all_prices.append(usd_cents / 100.0)

            # Track oldest date for pagination cursor
            date = tp.get("date")
            if date:
                if oldest_date is None or date < oldest_date:
                    oldest_date = date

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

    return all_prices


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
    os.makedirs(data_dir, exist_ok=True)

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

            print(f"Fetching {slug}...", end=" ", flush=True)
            prices = fetch_auction_prices(slug)
            prices.reverse()  # oldest first: 1st = first auction chronologically
            print(f"{len(prices)} auctions found")

            rows.append((display_name(slug, team), team, prices))

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


if __name__ == "__main__":
    main()
