import os, time
from pathlib import Path
import requests
import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta

# Ensure Streamlit can write configs (avoids '/.streamlit' issues on some hosts)
try:
    Path(".streamlit").mkdir(exist_ok=True)
    os.environ.setdefault("HOME", str(Path.cwd()))
    os.environ.setdefault("STREAMLIT_GLOBAL_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
    os.environ.setdefault("STREAMLIT_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
except Exception:
    pass

st.set_page_config(page_title="Solana Mini Dashboard", layout="wide")

# ---------------------------
# Small HTTP helper (cached + backoff)
# ---------------------------
@st.cache_data(ttl=600)
def http_json(url, params=None, tries=3, timeout=12):
    for i in range(tries):
        r = requests.get(url, params=params, timeout=timeout)
        # Simple backoff on 429 / 5xx
        if (r.status_code == 429 or 500 <= r.status_code < 600) and i < tries - 1:
            time.sleep(1.5 * (2 ** i))
            continue
        r.raise_for_status()
        return r.json()
    return None

# ---------------------------
# CoinGecko helpers (prices + history)
# ---------------------------
@st.cache_data(ttl=60)
def cg_simple_prices(ids_csv: str):
    js = http_json(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": ids_csv,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        },
    )
    return js or {}

@st.cache_data(ttl=600)
def cg_market_chart(coin_id: str, days: int = 90, vs="usd") -> pd.DataFrame:
    """Returns DataFrame with date, price for coin_id over N days."""
    js = http_json(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
        params={"vs_currency": vs, "days": days},
    )
    prices = (js or {}).get("prices", [])
    if not prices:
        return pd.DataFrame(columns=["date", "price"])
    df = pd.DataFrame(prices, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df[["date", "price"]]

@st.cache_data(ttl=1800)
def cg_market_caps_sum(ids=("tether", "usd-coin", "dai"), days=180) -> pd.DataFrame:
    """Sum market_caps for stablecoins to approximate global stablecoin liquidity."""
    out = None
    for coin in ids:
        js = http_json(
            f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": days},
        )
        caps = (js or {}).get("market_caps", [])
        if not caps:
            continue
        df = pd.DataFrame(caps, columns=["ts", f"cap_{coin}"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df[["date", f"cap_{coin}"]]
        out = df if out is None else pd.merge_asof(
            out.sort_values("date"),
            df.sort_values("date"),
            on="date",
            direction="nearest",
            tolerance=pd.Timedelta("1H"),
        )
    if out is None:
        return pd.DataFrame(columns=["date", "total_cap"])
    out["total_cap"] = out.drop(columns=["date"]).sum(axis=1, min_count=1)
    return out[["date", "total_cap"]].dropna()

# ---------------------------
# DeFiLlama helpers (TVL + stablecoin total)
# ---------------------------
@st.cache_data(ttl=1200)
def llama_solana_tvl() -> pd.DataFrame:
    js = http_json("https://api.llama.fi/v2/historicalChainTvl/Solana")
    if not js:
        return pd.DataFrame(columns=["date", "tvl"])
    df = pd.DataFrame(js)
    df["date"] = pd.to_datetime(df["date"], unit="s")
    return df[["date", "tvl"]]

@st.cache_data(ttl=1200)
def defillama_stablecoins_total():
    """Returns (total_float, raw_json)."""
    js = http_json("https://stablecoins.llama.fi/stablecoins")
    total = js.get("total") if isinstance(js, dict) else None
    return (float(total) if total is not None else None), js

@st.cache_data(ttl=600)
def cg_stablecoins_total_fallback():
    """Fallback single number using CoinGecko simple/price market_caps."""
    js = http_json(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "tether,usd-coin,dai", "vs_currencies": "usd", "include_market_cap": "true"},
    )
    def _cap(name):
        try:
            return float(js.get(name, {}).get("usd_market_cap"))
        except Exception:
            return None
    parts = [_cap("tether"), _cap("usd-coin"), _cap("dai")]
    parts = [p for p in parts if p is not None]
    return (sum(parts) if parts else None), js

# ---------------------------
# Utilities
# ---------------------------
def pct(x):
    try:
        return f"{x:+.2f}%"
    except Exception:
        return "—"

def usd_big(x):
    if x is None:
        return "—"
    try:
        xv = float(x)
        if xv >= 1e12:
            return f"${xv/1e12:,.2f}T"
        if xv >= 1e9:
            return f"${xv/1e9:,.1f}B"
        if xv >= 1e6:
            return f"${xv/1e6:,.0f}M"
        return f"${xv:,.0f}"
    except Exception:
        return "—"

def pct_change_over_days(df: pd.DataFrame, value_col: str, days: int = 30):
    """% change from first point inside the trailing N days window to the latest."""
    if df is None or df.empty or value_col not in df.columns:
        return None
    df = df[["date", value_col]].dropna()
    if df.empty:
        return None
    latest = df["date"].max()
    base_df = df[df["date"] >= latest - pd.Timedelta(days=days)]
    if base_df.empty:
        return None
    base = float(base_df.iloc[0][value_col])
    last = float(df.iloc[-1][value_col])
    if base == 0:
        return None
    return (last / base - 1) * 100.0

def line_chart(df, x, y, y_title, height=280):
    return alt.Chart(df).mark_line().encode(
        x=alt.X(f"{x}:T", title=""),
        y=alt.Y(f"{y}:Q", title=y_title),
        tooltip=[alt.Tooltip(f"{x}:T"), alt.Tooltip(f"{y}:Q", format=",.2f")],
    ).properties(height=height)

# ---------------------------
# Sidebar: refresh
# ---------------------------
with st.sidebar:
    st.header("Refresh")
    auto = st.checkbox("Auto-refresh", value=False)
    interval = st.slider("Interval (sec)", 15, 180, 60, 5)
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()

if auto:
    st.session_state.setdefault("tick", 0)
    if int(time.time()) % interval == 0:
        st.session_state["tick"] += 1

# ---------------------------
# Header + Price tiles
# ---------------------------
st.title("Solana Dashboard — v2")
st.caption(f"Local time (UTC+4): {datetime.now(timezone(timedelta(hours=4))).strftime('%Y-%m-%d %H:%M:%S')}")

try:
    data = cg_simple_prices("solana,ethereum,bitcoin")
    sol, eth, btc = data.get("solana", {}), data.get("ethereum", {}), data.get("bitcoin", {})
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("SOL (USD)", sol.get("usd", "—"), pct(sol.get("usd_24h_change", 0)))
    with c2: st.metric("ETH (USD)", eth.get("usd", "—"), pct(eth.get("usd_24h_change", 0)))
    with c3: st.metric("BTC (USD)", btc.get("usd", "—"), pct(btc.get("usd_24h_change", 0)))
except Exception as e:
    st.error(f"Price fetch failed: {e}")

st.divider()

# ---------------------------
# Charts — 90-day prices
# ---------------------------
st.subheader("90-Day Price Charts")
coin_map = {
    "Solana (SOL)": "solana",
    "Ethereum (ETH)": "ethereum",
    "Bitcoin (BTC)": "bitcoin",
}
left, right = st.columns([1, 3])

with left:
    choice = st.radio("Select asset", list(coin_map.keys()), index=0)
    st.caption("Data: CoinGecko /market_chart")

with right:
    cid = coin_map[choice]
    try:
        df = cg_market_chart(cid, days=90)
        if df.empty:
            st.info("No chart data (rate limited?). Try again later.")
        else:
            base = float(df.iloc[0]["price"])
            df["pct_from_start"] = (df["price"] / base - 1) * 100.0

            tab1, tab2 = st.tabs(["Price (USD)", "% from start"])
            with tab1:
                ch = line_chart(df, "date", "price", "Price (USD)", 300)
                st.altair_chart(ch, use_container_width=True)
            with tab2:
                ch2 = line_chart(df, "date", "pct_from_start", "% since first point", 300)
                st.altair_chart(ch2, use_container_width=True)
    except Exception as e:
        st.error(f"Chart error: {e}")

st.divider()

# ---------------------------
# NEW: On-chain Liquidity & TVL
# ---------------------------
st.subheader("On-chain Liquidity & TVL")

# Left column shows metrics; right column shows charts
mcol, gcol = st.columns([1, 2])

# --- Metrics (with 30d deltas)
with mcol:
    # Solana TVL (latest + 30d change)
    try:
        tvl_df = llama_solana_tvl()
        if tvl_df.empty:
            st.info("TVL unavailable right now.")
        else:
            tvl_30d = pct_change_over_days(tvl_df.rename(columns={"tvl": "value"}), "value", 30)
            latest_tvl = float(tvl_df.iloc[-1]["tvl"])
            st.metric("Solana TVL (latest)", f"${latest_tvl:,.0f}", pct(tvl_30d) if tvl_30d is not None else None)
    except Exception as e:
        st.error(f"TVL error: {e}")

    # Global stablecoin liquidity (best available) + 30d change
    try:
        # Try DeFiLlama point-in-time total first
        total_dl, raw_dl = defillama_stablecoins_total()
        # For the chart & 30d delta series, use CoinGecko summed caps (USDT+USDC+DAI)
        sc_df = cg_market_caps_sum(days=180)
        sc_delta = pct_change_over_days(sc_df.rename(columns={"total_cap": "value"}), "value", 30) if not sc_df.empty else None

        used_source = "DeFiLlama total"
        total_display = total_dl

        # If DeFiLlama total is missing, fallback to CoinGecko one-shot
        if total_display is None:
            total_cg, _raw_cg = cg_stablecoins_total_fallback()
            total_display = total_cg
            used_source = "CoinGecko (USDT+USDC+DAI)"

        st.metric("Stablecoin Liquidity (approx.)", usd_big(total_display), pct(sc_delta) if sc_delta is not None else None)
        st.caption(f"Source: {used_source}. Chart uses USDT+USDC+DAI sum as a liquidity proxy.")
    except Exception as e:
        st.error(f"Stablecoin liquidity error: {e}")

# --- Charts
with gcol:
    tabs = st.tabs(["Solana TVL (365d)", "Stablecoin Liquidity (180d)"])
    with tabs[0]:
        try:
            tvl_df = llama_solana_tvl()
            if tvl_df.empty:
                st.info("No TVL history to display.")
            else:
                ch_tvl = line_chart(tvl_df, "date", "tvl", "Solana TVL (USD)", 320)
                st.altair_chart(ch_tvl, use_container_width=True)
        except Exception as e:
            st.error(f"TVL chart error: {e}")

    with tabs[1]:
        try:
            sc_df = cg_market_caps_sum(days=180)
            if sc_df.empty:
                st.info("No stablecoin history (rate limited?).")
            else:
                ch_sc = line_chart(sc_df, "date", "total_cap", "USDT+USDC+DAI Market Cap (USD)", 320)
                st.altair_chart(ch_sc, use_container_width=True)
        except Exception as e:
            st.error(f"Stablecoin chart error: {e}")

st.caption("Tip: if you see 429 errors from CoinGecko, slow the refresh interval or try again later.")

