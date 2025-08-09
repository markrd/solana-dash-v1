import os, time
from pathlib import Path
import requests
import streamlit as st
from datetime import datetime, timezone, timedelta

# Make sure Streamlit writes configs to a writable folder (avoids '/.streamlit' issues)
try:
    Path(".streamlit").mkdir(exist_ok=True)
    os.environ.setdefault("HOME", str(Path.cwd()))
    os.environ.setdefault("STREAMLIT_GLOBAL_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
    os.environ.setdefault("STREAMLIT_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
except Exception:
    pass

st.set_page_config(page_title="Solana Dashboard", layout="wide")

@st.cache_data(ttl=60)
def cg_simple_prices(ids_csv: str):
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": ids_csv,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        },
            timeout=10
    )
    r.raise_for_status()
    return r.json()

# Sidebar: manual/auto refresh
with st.sidebar:
    st.header("Refresh")
    auto = st.checkbox("Auto-refresh", value=False)
    interval = st.slider("Interval (sec)", 15, 180, 60, 5)
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()

if auto:
    # Very light auto refresh (no extra package)
    # Just re-run when the time crosses the interval boundary
    st.session_state.setdefault("tick", 0)
    if int(time.time()) % interval == 0:
        st.session_state["tick"] += 1

# Header
st.title("Solana Mini Dashboard")
st.caption(f"Local time (UTC+4): {datetime.now(timezone(timedelta(hours=4))).strftime('%Y-%m-%d %H:%M:%S')}")

# Prices
try:
    data = cg_simple_prices("solana,ethereum,bitcoin")
    sol, eth, btc = data.get("solana", {}), data.get("ethereum", {}), data.get("bitcoin", {})
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("SOL (USD)", sol.get("usd", "—"), f"{sol.get('usd_24h_change',0):+.2f}% / 24h")
    with c2: st.metric("ETH (USD)", eth.get("usd", "—"), f"{eth.get('usd_24h_change',0):+.2f}% / 24h")
    with c3: st.metric("BTC (USD)", btc.get("usd", "—"), f"{btc.get('usd_24h_change',0):+.2f}% / 24h")
except Exception as e:
    st.error(f"Price fetch failed: {e}")

st.divider()
st.write("✅ Base app is up. Next we can add charts (Altair), TVL (DeFiLlama), and macro (FRED) once this runs reliably.")

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



