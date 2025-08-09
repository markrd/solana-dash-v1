import os
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

import requests
import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
import feedparser

st.set_page_config(page_title="Solana Dashboard v1", layout="wide")

FRED_API_KEY = os.getenv("FRED_API_KEY", "")

@st.cache_data(ttl=600)
def cg_prices():
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "solana,ethereum", "vs_currencies": "usd", "include_24hr_change": "true"},
        timeout=8,
    )
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=900)
def defillama_stables_total():
    r = requests.get("https://stablecoins.llama.fi/stablecoins", timeout=10)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=900)
def solana_tvl_series():
    r = requests.get("https://api.llama.fi/v2/historicalChainTvl/Solana", timeout=10)
    r.raise_for_status()
    js = r.json()
    if not js:
        return pd.DataFrame(columns=["date","tvl"])
    df = pd.DataFrame(js)
    df["date"] = pd.to_datetime(df["date"], unit="s")
    return df

@st.cache_data(ttl=3600)
def fred_series(series_id, observation_start="2015-01-01"):
    if not FRED_API_KEY:
        return None
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "observation_start": observation_start,
        },
        timeout=10,
    )
    r.raise_for_status()
    js = r.json()
    obs = js.get("observations", [])
    if not obs:
        return pd.DataFrame(columns=["date","value"])
    df = pd.DataFrame(obs)
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df

@st.cache_data(ttl=900)
def fetch_rss(url):
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries[:15]:
        items.append({"title": e.title, "link": e.link})
    return items

def fmt_billion(x):
    try:
        return f"${x/1e9:,.1f}B"
    except Exception:
        return "—"

st.title("Solana Morning Dashboard — v1")
col1, col2 = st.columns(2)
with col1: st.success("✅ App booted")
with col2: st.write(f"Local time (UTC+4): {datetime.now(timezone(timedelta(hours=4))).strftime('%Y-%m-%d %H:%M')}")
st.caption("Data: CoinGecko, DeFiLlama, FRED (optional), CoinDesk/The Block RSS.")

st.divider()

st.subheader("Live Prices (CoinGecko)")
try:
    data = cg_prices()
    sol, eth = data.get("solana", {}), data.get("ethereum", {})
    c1, c2 = st.columns(2)
    with c1:
        st.metric("SOL (USD)", sol.get("usd","—"), f"{sol.get('usd_24h_change',0):.2f}% / 24h")
    with c2:
        st.metric("ETH (USD)", eth.get("usd","—"), f"{eth.get('usd_24h_change',0):.2f}% / 24h")
except Exception as e:
    st.error(f"Price fetch failed: {e}")

st.divider()

lcol, rcol = st.columns([1,2])
with lcol:
    st.subheader("Stablecoin Liquidity (Total)")
    try:
        stables = defillama_stables_total()
        total = stables.get("total", None) if isinstance(stables, dict) else None
        st.metric("Stablecoin Market Cap (approx)", fmt_billion(total) if total else "—")
    except Exception as e:
        st.error(f"Stablecoin API error: {e}")

with rcol:
    st.subheader("Solana TVL")
    try:
        df = solana_tvl_series()
        if df.empty:
            st.warning("No TVL data returned (try refresh in a minute).")
        else:
            st.line_chart(df.set_index("date")["tvl"])
            st.metric("Latest TVL", f"${float(df.iloc[-1]['tvl']):,.0f}")
    except Exception as e:
        st.error(f"TVL fetch error: {e}")

st.divider()

st.subheader("Macro: PMI & M2 (FRED)")
if not FRED_API_KEY:
    st.info("Add FRED_API_KEY in Streamlit → Manage app → Settings → Secrets to enable PMI (NAPM) and M2 charts.")
else:
    c1, c2 = st.columns(2)
    with c1:
        pmi = fred_series("INDPRO", observation_start="2015-01-01")
        if pmi is None or pmi.empty:
            st.warning("PMI data unavailable.")
        else:
            st.line_chart(pmi.set_index("date")["value"], height=240)
            st.caption(f"Latest PMI: {pmi.dropna().iloc[-1]['value']:.1f} (50 = expansion)")
    with c2:
        m2 = fred_series("M2SL", observation_start="2015-01-01")
        if m2 is None or m2.empty:
            st.warning("M2 data unavailable.")
        else:
            st.line_chart(m2.set_index("date")["value"], height=240)
            st.caption("M2 money stock (not seasonally adjusted).")

st.divider()

st.subheader("News Watch")
left, right = st.columns(2)
with left:
    st.markdown("**CoinDesk**")
    try:
        for it in fetch_rss("https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml")[:8]:
            st.write(f"- [{it['title']}]({it['link']})")
    except Exception as e:
        st.error(f"CoinDesk RSS error: {e}")
with right:
    st.markdown("**The Block**")
    try:
        for it in fetch_rss("https://www.theblock.co/rss.xml")[:8]:
            st.write(f"- [{it['title']}]({it['link']})")
    except Exception as e:
        st.error(f"The Block RSS error: {e}")

st.divider()

st.subheader("10-Point Bullish Checklist")
checklist = [
    "Spot SOL ETF approved (US/EU)",
    "Firedancer client fully live on mainnet",
    "12+ months zero major outages",
    "Visa/Shopify/Stripe scale stablecoin settlement on Solana",
    "Helium data >1 PB/quarter",
    "Render usage >5× vs 2024 baseline",
    "Stablecoin transfer value leads on multiple days/month",
    "Solana DeFi TVL growth > ETH/L2s for 3+ quarters",
    "10M+ MAU consumer app on Solana",
    "US regulatory clarity: SOL explicitly not a security",
]
cols = st.columns(2)
met = 0
for i, item in enumerate(checklist):
    with cols[i % 2]:
        if st.checkbox(item, value=False, key=f"ck_{i}"):
            met += 1
st.info(f"Checklist met: **{met}/10**. Aim for 7+/10 with macro tailwinds for a strong bull case.")
