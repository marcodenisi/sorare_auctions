"""
One-off script to backfill all auction history using Sorare credentials.

Authenticates with your Sorare email/password to get a JWT token,
then fetches full auction history for all players with the higher
authenticated complexity budget (30,000 vs 500 unauthenticated).

Usage:
    python backfill.py

Your credentials are only used to obtain a session token and are
never stored.
"""

import getpass
import json
import os
import time

import bcrypt
import requests
import yaml

from fetch_auctions import (
    API_URL,
    BATCH_SIZE,
    POSITIONS,
    _make_price_record,
    load_history,
    name_from_slug,
    ordinal,
    save_history,
)

SLEEP_SECONDS = 1  # authenticated: 60 calls/min

SEASON = 2026

QUERY = """
query GetLimitedAuctionHistory($playerSlug: String!, $first: Int, $to: ISO8601DateTime, $season: Int) {
  tokens {
    tokenPrices(
      playerSlug: $playerSlug
      rarity: limited
      first: $first
      to: $to
      season: $season
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


def authenticate(email: str, password: str) -> tuple[str, str]:
    """Authenticate with Sorare and return (jwt_token, aud).

    Handles 2FA/OTP if enabled on the account.
    """
    aud = "sorare-auction-tracker"

    # Step 1: Get bcrypt salt
    print("Fetching password salt...")
    resp = requests.get(f"https://api.sorare.com/api/v1/users/{email}", timeout=30)
    resp.raise_for_status()
    salt = resp.json().get("salt")
    if not salt:
        raise RuntimeError("Could not retrieve salt. Check your email address.")

    # Step 2: Hash password
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt.encode("utf-8")).decode("utf-8")

    # Step 3: Sign in
    print("Signing in...")
    sign_in_mutation = """
    mutation SignInMutation($input: signInInput!) {
      signIn(input: $input) {
        currentUser { slug }
        jwtToken(aud: "%s") {
          token
          expiredAt
        }
        otpSessionChallenge
        errors { message }
      }
    }
    """ % aud

    resp = requests.post(
        API_URL,
        json={
            "query": sign_in_mutation,
            "variables": {"input": {"email": email, "password": hashed}},
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {}).get("signIn", {})

    # Check for 2FA before checking errors (2fa_missing comes as an error)
    otp_challenge = data.get("otpSessionChallenge")
    if otp_challenge and not data.get("currentUser"):
        otp_code = input("2FA code (check your email or authenticator app): ").strip()

        resp = requests.post(
            API_URL,
            json={
                "query": sign_in_mutation,
                "variables": {
                    "input": {
                        "otpSessionChallenge": otp_challenge,
                        "otpAttempt": otp_code,
                    }
                },
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("signIn", {})

        errors = data.get("errors", [])
        if errors:
            raise RuntimeError(f"2FA failed: {errors}")

    jwt_data = data.get("jwtToken", {})
    token = jwt_data.get("token")
    if not token:
        raise RuntimeError("No JWT token received. Sign-in may have failed.")

    user_slug = data.get("currentUser", {}).get("slug", "unknown")
    print(f"Authenticated as {user_slug} (token expires: {jwt_data.get('expiredAt')})")
    return token, aud


def fetch_all_auctions(slug: str, headers: dict) -> list[tuple[str, dict]]:
    """Fetch ALL auction history for a player using authenticated requests."""
    results: list[tuple[str, dict]] = []
    to_cursor: str | None = None
    prev_cursor: str | None = None
    page = 1

    while True:
        variables: dict = {"playerSlug": slug, "first": BATCH_SIZE, "season": SEASON}
        if to_cursor is not None:
            variables["to"] = to_cursor

        if page > 1:
            time.sleep(SLEEP_SECONDS)

        resp = requests.post(
            API_URL,
            json={"query": QUERY, "variables": variables},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()

        for err in body.get("errors", []):
            msg = err.get("message", "")
            if "complexity" in msg.lower():
                print(f"\n    Complexity error on page {page} (unexpected with auth)")
                return results
            print(f"\n    API error: {msg}", end=" ")

        data = body.get("data") or {}
        tokens = data.get("tokens") or {}
        token_prices = tokens.get("tokenPrices") or []
        if not token_prices:
            break

        oldest_date = None
        page_auctions = 0
        for tp in token_prices:
            deal = tp.get("deal")
            if deal and deal.get("id"):
                price_record = _make_price_record(tp["amounts"])
                date = tp.get("date", "")
                results.append((date, price_record))
                page_auctions += 1

            d = tp.get("date")
            if d and (oldest_date is None or d < oldest_date):
                oldest_date = d

        if page > 1:
            print(f"page {page} ({page_auctions} auctions)", end=" ", flush=True)

        if len(token_prices) < BATCH_SIZE:
            break

        if oldest_date is None or oldest_date == prev_cursor:
            break
        prev_cursor = to_cursor
        to_cursor = oldest_date
        page += 1

    return results


def main() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    players_path = os.path.join(base_dir, "players.yaml")
    data_dir = os.path.join(base_dir, "data")
    history_dir = os.path.join(data_dir, "history")
    os.makedirs(history_dir, exist_ok=True)

    # Authenticate
    email = input("Sorare email: ").strip()
    password = getpass.getpass("Sorare password: ")
    token, aud = authenticate(email, password)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "JWT-AUD": aud,
    }

    with open(players_path, "r") as f:
        players_data = yaml.safe_load(f)

    start_time = time.time()
    total_players = 0
    total_auctions = 0

    for pos in POSITIONS:
        players = players_data.get(pos, [])
        if not players:
            continue

        print(f"\n=== {pos.upper()} ({len(players)} players) ===")

        for i, p in enumerate(players):
            slug = p["slug"]
            team = p["team"]

            if i > 0:
                time.sleep(SLEEP_SECONDS)

            history_path = os.path.join(history_dir, f"{slug}.json")
            history = load_history(history_path)

            print(f"  {slug}...", end=" ", flush=True)
            auctions = fetch_all_auctions(slug, headers)

            new_count = 0
            for date, price_record in auctions:
                if date not in history:
                    new_count += 1
                history[date] = price_record

            save_history(history_path, history)

            total_players += 1
            total_auctions += len(history)
            print(f"{len(history)} total ({new_count} new)")

    elapsed = time.time() - start_time
    print(f"\nDone in {elapsed:.1f}s — {total_players} players, {total_auctions} total auctions")
    print("Run 'python fetch_auctions.py' to regenerate CSVs, then commit and push.")


if __name__ == "__main__":
    main()
