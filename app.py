"""Streamlit dashboard for Sorare MLS Limited Auctions.

Reads auction history from JSON files (data/history/*.json) and
players.yaml to build position-grouped tables with multi-currency
support.
"""

import json
import os
import statistics
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st
import yaml

from utils import name_from_slug

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




def _compute_value(prices: list[float]) -> str:
    """Compute value indicator comparing recent prices to historical average.

    Returns "BUY" if recent prices are >1 std dev below average,
    "watch" if >0.5 std dev below, or empty string otherwise.
    """
    valid = [p for p in prices if p is not None and not pd.isna(p)]
    if len(valid) < 4:
        return ""
    avg = sum(valid) / len(valid)
    stdev = statistics.stdev(valid)
    if stdev == 0:
        return ""
    recent_avg = sum(valid[-3:]) / 3
    score = (avg - recent_avg) / stdev
    if score > 1.0:
        return "BUY"
    if score > 0.5:
        return "watch"
    return ""


def _relative_time(iso_date: str) -> str:
    """Return a human-readable relative time string like '2h ago' or '3d ago'."""
    dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _load_player_history(
    slug: str, currency_key: str
) -> tuple[list[str], list[float | None]]:
    """Load a player's history JSON and extract dates and prices.

    Returns (dates, prices) sorted by date ascending.  Old-format entries
    (bare floats) are treated as USD.
    """
    path = os.path.join(HISTORY_DIR, f"{slug}.json")
    if not os.path.isfile(path):
        return [], []

    with open(path, "r") as f:
        raw = json.load(f)

    dates = []
    prices = []
    for date in sorted(raw.keys()):
        entry = raw[date]
        dates.append(date)
        if isinstance(entry, dict):
            prices.append(entry.get(currency_key))
        else:
            # Legacy format: bare float = USD
            if currency_key == "usd":
                prices.append(float(entry))
            else:
                prices.append(None)
    return dates, prices


def _load_and_prepare(position: str) -> tuple[pd.DataFrame, dict] | None:
    """Load history JSONs for all players in a position and build the display DataFrame.

    Returns (DataFrame, player_details) or None if no data available.
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

        dates, prices = _load_player_history(slug, currency_key)
        max_prices = max(max_prices, len(prices))

        valid_prices = [v for v in prices if v is not None]
        avg_price = sum(valid_prices) / len(valid_prices) if valid_prices else 0.0
        trend = _compute_trend(prices)
        value = _compute_value(prices)
        last_auction = _relative_time(dates[-1]) if dates else "—"

        rows.append({
            "slug": slug,
            "display_name": display_name,
            "team": team,
            "trend": trend,
            "value": value,
            "avg_price": avg_price,
            "avg_price_raw": avg_price,
            "prices": prices,
            "dates": dates,
            "last_auction": last_auction,
        })

    if not rows:
        return None

    # Build output dicts and per-player detail data
    out_rows = []
    player_details = {}  # display_name -> {dates, prices, valid_prices}
    for r in rows:
        valid = [v for v in r["prices"] if v is not None]
        # Sparkline: all valid prices (ascending order, oldest to newest)
        sparkline = valid if valid else []
        # Last 5 auction prices (most recent first)
        last_5 = list(reversed(valid[-5:])) if valid else []

        out = {
            "Player": r["display_name"],
            "Team": r["team"],
            "Trend": r["trend"],
            "Value": r["value"],
            "Last Auction": r["last_auction"],
            "Avg Price": r["avg_price"],
            "Price History": sparkline,
        }
        recent_labels = ["Latest", "2nd Last", "3rd Last", "4th Last", "5th Last"]
        for i, col in enumerate(recent_labels):
            out[col] = last_5[i] if i < len(last_5) else None
        out["_avg_sort"] = r["avg_price_raw"]
        out_rows.append(out)
        player_details[r["display_name"]] = {
            "dates": r["dates"],
            "prices": r["prices"],
            "valid_prices": valid,
        }

    result = pd.DataFrame(out_rows)

    # Sort by average price descending
    result = result.sort_values("_avg_sort", ascending=False).reset_index(drop=True)
    result = result.drop(columns=["_avg_sort"])

    # Format prices for display
    result["Avg Price"] = result["Avg Price"].apply(_format_price)
    for col in ["Latest", "2nd Last", "3rd Last", "4th Last", "5th Last"]:
        if col in result.columns:
            result[col] = result[col].apply(
                lambda v: _format_price(v) if v is not None else ""
            )

    return result, player_details


tab_objects = st.tabs(list(TABS.keys()))

for tab, (label, position) in zip(tab_objects, TABS.items()):
    with tab:
        loaded = _load_and_prepare(position)
        if loaded is None:
            st.warning("No data. Run fetch_auctions.py first.")
            continue
        df, player_details = loaded
        if df.empty:
            st.warning("No data. Run fetch_auctions.py first.")
            continue

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

        # CSV export
        csv_data = filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download CSV",
            csv_data,
            file_name=f"sorare_{position}_{selected_currency.lower()}.csv",
            mime="text/csv",
            key=f"{label}_csv",
        )

        col_config = {
            "Player": st.column_config.TextColumn(width=180),
            "Team": st.column_config.TextColumn(width=60),
            "Trend": st.column_config.TextColumn(width=60),
            "Value": st.column_config.TextColumn(width=60),
            "Last Auction": st.column_config.TextColumn(width=100),
            "Avg Price": st.column_config.TextColumn(width=90),
            "Price History": st.column_config.LineChartColumn(
                "Price History", width=150,
            ),
            "Latest": st.column_config.TextColumn(width=80),
            "2nd Last": st.column_config.TextColumn(width=80),
            "3rd Last": st.column_config.TextColumn(width=80),
            "4th Last": st.column_config.TextColumn(width=80),
            "5th Last": st.column_config.TextColumn(width=80),
        }

        st.dataframe(
            filtered,
            width="stretch",
            hide_index=True,
            column_config=col_config,
        )

        # Player detail section: stats + chart
        player_names = filtered["Player"].tolist()
        if player_names:
            selected_player = st.selectbox(
                "Player details",
                player_names,
                key=f"{label}_detail",
            )
            detail = player_details.get(selected_player)
            if detail and detail["valid_prices"]:
                vp = detail["valid_prices"]
                with st.expander(f"{selected_player} — Stats & Price Chart", expanded=True):
                    # Stats
                    cols = st.columns(5)
                    cols[0].metric("Auctions", len(vp))
                    cols[1].metric("Min", _format_price(min(vp)))
                    cols[2].metric("Max", _format_price(max(vp)))
                    cols[3].metric("Median", _format_price(statistics.median(vp)))
                    if len(vp) >= 2:
                        cols[4].metric("Std Dev", _format_price(statistics.stdev(vp)))
                    else:
                        cols[4].metric("Std Dev", "—")

                    # Price chart
                    chart_data = [
                        {"Date": d, "Price": p}
                        for d, p in zip(detail["dates"], detail["prices"])
                        if p is not None
                    ]
                    if chart_data:
                        chart_df = pd.DataFrame(chart_data)
                        chart_df["Date"] = pd.to_datetime(chart_df["Date"])
                        chart = (
                            alt.Chart(chart_df)
                            .mark_line(point=True)
                            .encode(
                                x=alt.X("Date:T", title="Date"),
                                y=alt.Y("Price:Q", title=f"Price ({selected_currency})", scale=alt.Scale(zero=False)),
                                tooltip=[
                                    alt.Tooltip("Date:T", format="%b %d, %H:%M"),
                                    alt.Tooltip("Price:Q", format=",.2f", title=f"Price ({selected_currency})"),
                                ],
                            )
                            .interactive()
                        )
                        st.altair_chart(chart, use_container_width=True)

# ---------------------------------------------------------------------------
# Compare (persistent section below tabs)
# ---------------------------------------------------------------------------
st.divider()
with st.expander("Compare Players Across Positions", expanded=False):
    if not os.path.isfile(PLAYERS_PATH):
        st.warning("No data. Run fetch_auctions.py first.")
    else:
        with open(PLAYERS_PATH, "r") as f:
            all_players_data = yaml.safe_load(f)

        # Build flat list of all players across positions
        all_players = []
        for pos in TABS.values():
            for p in all_players_data.get(pos, []):
                display = name_from_slug(p["slug"])
                all_players.append({"slug": p["slug"], "team": p["team"], "name": display})

        player_options = [f"{p['name']} ({p['team']})" for p in all_players]

        selected = st.multiselect(
            "Select players to compare (2-4)",
            player_options,
            max_selections=4,
            key="compare_players",
        )

        if len(selected) >= 2:
            currency_key = currency_cfg["key"]
            chart_rows = []
            stats_rows = []

            for label_str in selected:
                idx = player_options.index(label_str)
                p = all_players[idx]
                dates, prices = _load_player_history(p["slug"], currency_key)
                valid = [v for v in prices if v is not None]

                for d, price in zip(dates, prices):
                    if price is not None:
                        chart_rows.append({"Date": d, "Price": price, "Player": p["name"]})

                if valid:
                    stats_rows.append({
                        "Player": p["name"],
                        "Team": p["team"],
                        "Avg": _format_price(sum(valid) / len(valid)),
                        "Min": _format_price(min(valid)),
                        "Max": _format_price(max(valid)),
                        "Median": _format_price(statistics.median(valid)),
                        "Std Dev": _format_price(statistics.stdev(valid)) if len(valid) >= 2 else "—",
                        "Auctions": len(valid),
                    })

            if chart_rows:
                chart_df = pd.DataFrame(chart_rows)
                chart_df["Date"] = pd.to_datetime(chart_df["Date"])
                chart = (
                    alt.Chart(chart_df)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("Date:T", title="Date"),
                        y=alt.Y("Price:Q", title=f"Price ({selected_currency})", scale=alt.Scale(zero=False)),
                        color=alt.Color("Player:N"),
                        tooltip=[
                            alt.Tooltip("Player:N"),
                            alt.Tooltip("Date:T", format="%b %d, %H:%M"),
                            alt.Tooltip("Price:Q", format=",.2f", title=f"Price ({selected_currency})"),
                        ],
                    )
                    .interactive()
                )
                st.altair_chart(chart, use_container_width=True)

            if stats_rows:
                st.dataframe(
                    pd.DataFrame(stats_rows),
                    hide_index=True,
                    use_container_width=True,
                )
        elif len(selected) == 1:
            st.info("Select at least 2 players to compare.")
