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
# CoinGecko helpers (cached)
# ---------------------------
@st.cache_data(ttl=60)
def cg_simple_prices(ids_csv: str):
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": ids_csv,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=600)
def cg_market_chart(coin_id: str, days: int = 90, vs="usd") -> pd.DataFrame:
    """Returns DataFrame with date, price for coin_id over N days."""
    r = requests.get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
        params={"vs_currency": vs, "days": days},
        timeout=12,
    )
    r.raise_for_status()
    js = r.json()
    prices = js.get("prices", [])
    if not prices:
        return pd.DataFrame(columns=["date", "price"])
    df = pd.DataFrame(prices, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df[["date", "price"]]

def pct(x):
    try: return f"{x:+.2f}%"
    except: return "—"

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
st.title("Solana Mini Dashboard")
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
                ch = alt.Chart(df).mark_line().encode(
                    x=alt.X("date:T", title=""),
                    y=alt.Y("price:Q", title="Price (USD)"),
                    tooltip=[alt.Tooltip("date:T"), alt.Tooltip("price:Q", format=",.2f")],
                ).properties(height=300)
                st.altair_chart(ch, use_container_width=True)

            with tab2:
                ch2 = alt.Chart(df).mark_line().encode(
                    x=alt.X("date:T", title=""),
                    y=alt.Y("pct_from_start:Q", title="% since first point"),
                    tooltip=[alt.Tooltip("date:T"), alt.Tooltip("pct_from_start:Q", format=".2f")],
                ).properties(height=300)
                st.altair_chart(ch2, use_container_width=True)
    except Exception as e:
        st.error(f"Chart error: {e}")

st.divider()
st.write("✅ Charts added. Next we can layer in Solana TVL (DeFiLlama), stablecoin liquidity, and macro (FRED) once you confirm this renders.")
