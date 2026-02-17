"""Streamlit dashboard for Sorare MLS Limited Auctions.

Reads auction history from JSON files (data/history/*.json) and
players.yaml to build position-grouped tables with multi-currency
support.
"""

import json
import os
import re

import pandas as pd
import streamlit as st
import yaml

st.set_page_config(page_title="Sorare MLS Limited Auctions", layout="wide")
st.title("Sorare MLS Limited Auctions")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
PLAYERS_PATH = os.path.join(os.path.dirname(__file__), "players.yaml")

last_updated_path = os.path.join(DATA_DIR, "last_updated.txt")
if os.path.isfile(last_updated_path):
    with open(last_updated_path) as f:
        st.caption(f"Last updated: {f.read().strip()}")

TABS = {
    "Goalkeepers": "gk",
    "Defenders": "df",
    "Midfielders": "mf",
    "Forwards": "fw",
}

CURRENCIES = {
    "USD": {"key": "usd", "symbol": "$", "decimals": 2},
    "EUR": {"key": "eur", "symbol": "\u20ac", "decimals": 2},
    "GBP": {"key": "gbp", "symbol": "\u00a3", "decimals": 2},
    "ETH": {"key": "eth", "symbol": "\u039e", "decimals": 4},
}


# ---------------------------------------------------------------------------
# Currency selector (at top, before tabs)
# ---------------------------------------------------------------------------
selected_currency = st.radio(
    "Currency",
    list(CURRENCIES.keys()),
    horizontal=True,
    index=0,
)
currency_cfg = CURRENCIES[selected_currency]


def _format_price(value: float) -> str:
    """Format a numeric price for the selected currency, or empty string if NaN/None."""
    if value is None or pd.isna(value):
        return ""
    decimals = currency_cfg["decimals"]
    symbol = currency_cfg["symbol"]
    return f"{symbol}{value:,.{decimals}f}"


def _compute_trend(prices: list[float]) -> str:
    """Compute trend indicator comparing recent 3 auctions to overall average.

    Returns:
        A trend string: up-arrow, down-arrow, right-arrow, or dash.
    """
    valid = [p for p in prices if p is not None and not pd.isna(p)]
    if len(valid) < 4:
        return "\u2014"  # em-dash
    overall_avg = sum(valid) / len(valid)
    recent_avg = sum(valid[-3:]) / 3
    if overall_avg == 0:
        return "\u2192"
    ratio = (recent_avg - overall_avg) / overall_avg
    if ratio > 0.05:
        return "\u2191"
    if ratio < -0.05:
        return "\u2193"
    return "\u2192"


def name_from_slug(slug: str) -> str:
    """Derive a display name from a slug like 'roman-celentano' -> 'Roman Celentano'."""
    cleaned = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", slug)
    return " ".join(part.capitalize() for part in cleaned.split("-"))


def _ordinal(n: int) -> str:
    """Return ordinal string for a 1-based index: 1 -> '1st', 2 -> '2nd', ..."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _load_player_history(slug: str, currency_key: str) -> list[float | None]:
    """Load a player's history JSON and extract prices for the given currency.

    Returns prices sorted by date (ascending).  Old-format entries
    (bare floats) are treated as USD.
    """
    path = os.path.join(HISTORY_DIR, f"{slug}.json")
    if not os.path.isfile(path):
        return []

    with open(path, "r") as f:
        raw = json.load(f)

    prices = []
    for date in sorted(raw.keys()):
        entry = raw[date]
        if isinstance(entry, dict):
            prices.append(entry.get(currency_key))
        else:
            # Legacy format: bare float = USD
            if currency_key == "usd":
                prices.append(float(entry))
            else:
                prices.append(None)
    return prices


def _load_and_prepare(position: str) -> pd.DataFrame | None:
    """Load history JSONs for all players in a position and build the display DataFrame.

    Returns None if players.yaml does not exist or the position has no players.
    """
    if not os.path.isfile(PLAYERS_PATH):
        return None

    with open(PLAYERS_PATH, "r") as f:
        players_data = yaml.safe_load(f)

    players = players_data.get(position, [])
    if not players:
        return None

    currency_key = currency_cfg["key"]

    rows = []
    max_prices = 0

    for p in players:
        slug = p["slug"]
        team = p["team"]
        display_name = name_from_slug(slug)

        prices = _load_player_history(slug, currency_key)
        max_prices = max(max_prices, len(prices))

        valid_prices = [v for v in prices if v is not None]
        avg_price = sum(valid_prices) / len(valid_prices) if valid_prices else 0.0
        trend = _compute_trend(prices)

        rows.append({
            "slug": slug,
            "display_name": display_name,
            "team": team,
            "trend": trend,
            "avg_price": avg_price,
            "avg_price_raw": avg_price,
            "prices": prices,
        })

    if not rows:
        return None

    # Build ordinal column names
    price_cols = [_ordinal(n) for n in range(1, max_prices + 1)]

    # Build output dicts
    out_rows = []
    for r in rows:
        out = {
            "Player": r["display_name"],
            "Team": r["team"],
            "Trend": r["trend"],
            "Avg Price": r["avg_price"],
        }
        for i, col in enumerate(price_cols):
            if i < len(r["prices"]):
                out[col] = r["prices"][i]
            else:
                out[col] = None
        out["_avg_sort"] = r["avg_price_raw"]
        out_rows.append(out)

    result = pd.DataFrame(out_rows)

    # Sort by average price descending
    result = result.sort_values("_avg_sort", ascending=False).reset_index(drop=True)
    result = result.drop(columns=["_avg_sort"])

    # Format prices for display
    result["Avg Price"] = result["Avg Price"].apply(_format_price)
    for col in price_cols:
        if col in result.columns:
            result[col] = result[col].apply(
                lambda v: _format_price(v) if v is not None else ""
            )

    return result


tab_objects = st.tabs(list(TABS.keys()))

for tab, (label, position) in zip(tab_objects, TABS.items()):
    with tab:
        df = _load_and_prepare(position)
        if df is None or df.empty:
            st.warning("No data. Run fetch_auctions.py first.")
        else:
            # Filters
            col1, col2 = st.columns(2)
            with col1:
                teams = sorted(df["Team"].unique())
                selected_teams = st.multiselect(
                    "Team", teams, default=teams, key=f"{label}_team"
                )
            with col2:
                player_search = st.text_input(
                    "Player", placeholder="Search...", key=f"{label}_player"
                )

            filtered = df[df["Team"].isin(selected_teams)]
            if player_search:
                filtered = filtered[
                    filtered["Player"].str.contains(player_search, case=False, na=False)
                ]

            col_config = {
                "Player": st.column_config.TextColumn(width=180),
                "Team": st.column_config.TextColumn(width=60),
                "Trend": st.column_config.TextColumn(width=60),
                "Avg Price": st.column_config.TextColumn(width=90),
            }
            # Narrow price columns
            for col in filtered.columns:
                if col not in col_config:
                    col_config[col] = st.column_config.TextColumn(width=80)

            st.dataframe(
                filtered,
                width="stretch",
                hide_index=True,
                column_config=col_config,
            )
