"""Streamlit dashboard for Sorare MLS Limited Auctions."""

import os

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Sorare MLS Limited Auctions", layout="wide")
st.title("Sorare MLS Limited Auctions")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

TABS = {
    "LimitedGK": "limited_gk.csv",
    "LimitedDF": "limited_df.csv",
    "LimitedMF": "limited_mf.csv",
    "LimitedFW": "limited_fw.csv",
}


def _format_price(value: float) -> str:
    """Format a numeric price as $XXX.XX, or empty string if NaN."""
    if pd.isna(value):
        return ""
    return f"${value:,.2f}"


def _compute_trend(prices: list[float]) -> str:
    """Compute trend indicator comparing recent 3 auctions to overall average.

    Returns:
        A trend string: up-arrow, down-arrow, right-arrow, or dash.
    """
    valid = [p for p in prices if not pd.isna(p)]
    if len(valid) < 4:
        return "\u2014"  # em-dash
    overall_avg = sum(valid) / len(valid)
    recent_avg = sum(valid[:3]) / 3
    if overall_avg == 0:
        return "\u2192"
    ratio = (recent_avg - overall_avg) / overall_avg
    if ratio > 0.05:
        return "\u2191"
    if ratio < -0.05:
        return "\u2193"
    return "\u2192"


def _load_and_prepare(csv_path: str) -> pd.DataFrame | None:
    """Load a CSV file and prepare the display DataFrame.

    Returns None if the file does not exist.
    """
    if not os.path.isfile(csv_path):
        return None

    df = pd.read_csv(csv_path)

    # Identify ordinal price columns (everything after player, team, role)
    price_cols = [c for c in df.columns if c not in ("player", "team", "role")]

    # Build the output rows
    rows = []
    for _, row in df.iterrows():
        prices = [row[c] if c in row.index else float("nan") for c in price_cols]
        valid_prices = [p for p in prices if not pd.isna(p)]

        avg_price = sum(valid_prices) / len(valid_prices) if valid_prices else 0.0
        trend = _compute_trend(prices)

        out = {
            "Player": row["player"],
            "Proj Role": row["role"],
            "Trend": trend,
            "Avg Price": avg_price,
        }
        for col in price_cols:
            val = row[col] if col in row.index else float("nan")
            out[col] = val if not pd.isna(val) else None

        rows.append(out)

    result = pd.DataFrame(rows)

    # Sort by average price descending
    result = result.sort_values("Avg Price", ascending=False).reset_index(drop=True)

    # Format prices for display
    result["Avg Price"] = result["Avg Price"].apply(_format_price)
    for col in price_cols:
        if col in result.columns:
            result[col] = result[col].apply(
                lambda v: _format_price(v) if v is not None else ""
            )

    return result


tab_objects = st.tabs(list(TABS.keys()))

for tab, (label, filename) in zip(tab_objects, TABS.items()):
    with tab:
        csv_path = os.path.join(DATA_DIR, filename)
        df = _load_and_prepare(csv_path)
        if df is None or df.empty:
            st.warning("No data. Run fetch_auctions.py first.")
        else:
            st.dataframe(df, width="stretch", hide_index=True)
