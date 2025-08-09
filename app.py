import os, time, base64, json
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

import requests
import pandas as pd
import streamlit as st
import altair as alt
from datetime import datetime, date, timezone, timedelta
from dateutil.relativedelta import relativedelta
from streamlit_autorefresh import st_autorefresh

# --------------------
# Page & Style
# --------------------
st.set_page_config(page_title="Solana Macro Dashboard", layout="wide", initial_sidebar_state="expanded")

# Minimal modern CSS
st.markdown("""
<style>
:root { --card-bg: #0e1117; --card-pad: 1rem; --card-radius: 16px; --shadow: 0 8px 30px rgba(0,0,0,0.12); }
.block-container { padding-top: 1.2rem; }
.card { background: var(--card-bg); padding: var(--card-pad); border-radius: var(--card-radius); box-shadow: var(--shadow); }
.metric { font-size: 1.7rem; font-weight: 700; }
.caption { color: #9aa3ab; font-size: 0.85rem; }
hr { border: none; border-top: 1px solid rgba(255,255,255,0.08); margin: 1.2rem 0; }
</style>
""", unsafe_allow_html=True)

# --------------------
# Sidebar controls
# --------------------
with st.sidebar:
    st.header("Controls")
    auto = st.checkbox("Auto-refresh", value=False)
    interval = st.slider("Interval (sec)", 15, 180, 60, 5)
    if auto: st_autorefresh(interval=interval*1000, key="auto_refresh_tick")
    st.caption(f"Last refreshed (UTC+4): {(datetime.now(timezone(timedelta(hours=4))).strftime('%Y-%m-%d %H:%M:%S'))}")

FRED_API_KEY = os.getenv("FRED_API_KEY", "")

# --------------------
# Helpers (cached + backoff)
# --------------------
@st.cache_data(ttl=600)
def http_json(url, params=None, tries=3, timeout=12):
    for i in range(tries):
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code == 429 and i < tries - 1:
            time.sleep(1.5*(2**i))
            continue
        r.raise_for_status()
        return r.json()
    return None

@st.cache_data(ttl=600)
def cg_simple_prices(ids_csv: str):
    return http_json(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ids_csv, "vs_currencies": "usd", "include_24hr_change": "true"}
    ) or {}

@st.cache_data(ttl=1800)
def cg_market_chart(coin_id: str, days: int=370, vs="usd"):
    js = http_json(f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
                   params={"vs_currency": vs, "days": days})
    prices = (js or {}).get("prices", [])
    if not prices:
        return pd.DataFrame(columns=["date","price"])
    df = pd.DataFrame(prices, columns=["ts","price"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df[["date","price"]]

@st.cache_data(ttl=1200)
def defillama_stablecoins_total():
    js = http_json("https://stablecoins.llama.fi/stablecoins")
    total = js.get("total") if isinstance(js, dict) else None
    return float(total) if total is not None else None, js

@st.cache_data(ttl=900)
def llama_solana_tvl():
    js = http_json("https://api.llama.fi/v2/historicalChainTvl/Solana")
    if not js: return pd.DataFrame(columns=["date","tvl"])
    df = pd.DataFrame(js)
    df["date"] = pd.to_datetime(df["date"], unit="s")
    return df[["date","tvl"]]

@st.cache_data(ttl=3600)
def fred_series(series_id, start="2015-01-01"):
    if not FRED_API_KEY: return pd.DataFrame(columns=["date","value"])
    js = http_json("https://api.stlouisfed.org/fred/series/observations", {
        "series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json", "observation_start": start
    })
    obs = (js or {}).get("observations", [])
    if not obs: return pd.DataFrame(columns=["date","value"])
    df = pd.DataFrame(obs)
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna()

# PMI fallback: try ISM NAPM, then Markit PMI, else use INDPRO (proxy)
@st.cache_data(ttl=3600)
def macro_pmi_ytd():
    # try NAPM
    for sid, label in [("NAPM","ISM PMI"), ("PMI","Markit PMI"), ("INDPRO","Industrial Production (proxy)")]:
        df = fred_series(sid, start=str(date.today().year-5)+"-01-01")
        if not df.empty:
            return df, label
    return pd.DataFrame(columns=["date","value"]), "PMI"

@st.cache_data(ttl=3600)
def macro_m2_ytd():
    return fred_series("M2SL", start=str(date.today().year-5)+"-01-01")

def ytd_only(df, date_col="date"):
    if df is None or df.empty: return df
    start = pd.Timestamp(f"{date.today().year}-01-01")
    return df[df[date_col] >= start].copy()

def ytd_change(df, value_col="value"):
    if df is None or df.empty: return None
    base = float(df.iloc[0][value_col])
    last = float(df.iloc[-1][value_col])
    if base == 0: return None
    return (last/base - 1) * 100.0

def pctfmt(x):
    return "—" if x is None else f"{x:+.1f}%"

def usd_big(x):
    if x is None: return "—"
    try:
        x=float(x)
        if x>=1e12: return f"${x/1e12:,.2f}T"
        if x>=1e9:  return f"${x/1e9:,.1f}B"
        if x>=1e6:  return f"${x/1e6:,.0f}M"
        return f"${x:,.0f}"
    except:
        return "—"

# --------------------
# Header
# --------------------
header = st.container()
with header:
    c1,c2,c3,c4 = st.columns([1.2,1,1,1])
    with c1:
        st.markdown("<div class='card'><div class='metric'>Solana Macro Dashboard</div><div class='caption'>YTD changes for macro & crypto + on-chain context</div></div>", unsafe_allow_html=True)
    # live prices (SOL/ETH/BTC)
    prices = cg_simple_prices("solana,ethereum,bitcoin")
    sol = prices.get("solana",{}); eth = prices.get("ethereum",{}); btc = prices.get("bitcoin",{})
    with c2: st.markdown(f"<div class='card'><div class='caption'>SOL</div><div class='metric'>{sol.get('usd','—')}</div><div class='caption'>{sol.get('usd_24h_change',0):+.1f}% / 24h</div></div>", unsafe_allow_html=True)
    with c3: st.markdown(f"<div class='card'><div class='caption'>ETH</div><div class='metric'>{eth.get('usd','—')}</div><div class='caption'>{eth.get('usd_24h_change',0):+.1f}% / 24h</div></div>", unsafe_allow_html=True)
    with c4: st.markdown(f"<div class='card'><div class='caption'>BTC</div><div class='metric'>{btc.get('usd','—')}</div><div class='caption'>{btc.get('usd_24h_change',0):+.1f}% / 24h</div></div>", unsafe_allow_html=True)

st.markdown("<hr/>", unsafe_allow_html=True)

# --------------------
# YTD Charts (PMI, M2, SOL, ETH, BTC)
# --------------------
st.subheader("YTD (in-year) changes")

left, right = st.columns(2)

with left:
    # PMI (or fallback)
    pmi_df, pmi_label = macro_pmi_ytd()
    pmi_ytd = ytd_only(pmi_df)
    pmi_change = ytd_change(pmi_ytd, "value")
    st.markdown(f"**{pmi_label} — YTD change: {pctfmt(pmi_change)}**")
    if not pmi_ytd.empty:
        ch = alt.Chart(pmi_ytd).mark_line().encode(
            x=alt.X("date:T", title=""),
            y=alt.Y("value:Q", title=pmi_label),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("value:Q", format=".2f")]
        ).properties(height=260)
        st.altair_chart(ch, use_container_width=True)
    else:
        st.info("No PMI/INDPRO data available (check FRED key/series access).")

    # SOL YTD
    sol_hist = cg_market_chart("solana", days=370)
    sol_ytd = ytd_only(sol_hist, "date")
    if not sol_ytd.empty:
        base_sol = float(sol_ytd.iloc[0]["price"])
        sol_ytd["change_pct"] = (sol_ytd["price"]/base_sol - 1)*100
        st.markdown(f"**SOL — YTD change: {pctfmt(float(sol_ytd['change_pct'].iloc[-1]))}**")
        ch = alt.Chart(sol_ytd).mark_line().encode(
            x=alt.X("date:T", title=""),
            y=alt.Y("change_pct:Q", title="SOL % from Jan 1"),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("change_pct:Q", format=".1f")]
        ).properties(height=260)
        st.altair_chart(ch, use_container_width=True)
    else:
        st.info("No SOL history (rate limit?)")

with right:
    # M2 YTD
    m2_df = macro_m2_ytd()
    m2_ytd = ytd_only(m2_df)
    m2_change = ytd_change(m2_ytd, "value")
    st.markdown(f"**M2 Money Stock — YTD change: {pctfmt(m2_change)}**")
    if not m2_ytd.empty:
        ch = alt.Chart(m2_ytd).mark_line().encode(
            x=alt.X("date:T", title=""),
            y=alt.Y("value:Q", title="M2 (NSA)"),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("value:Q", format=".2f")]
        ).properties(height=260)
        st.altair_chart(ch, use_container_width=True)
    else:
        st.info("No M2 data (check FRED key).")

    # ETH + BTC YTD (two small charts)
    for coin_id, label in [("ethereum","ETH"), ("bitcoin","BTC")]:
        hist = cg_market_chart(coin_id, days=370)
        ytd = ytd_only(hist, "date")
        if not ytd.empty:
            base = float(ytd.iloc[0]["price"])
            ytd["change_pct"] = (ytd["price"]/base - 1)*100
            st.markdown(f"**{label} — YTD change: {pctfmt(float(ytd['change_pct'].iloc[-1]))}**")
            ch = alt.Chart(ytd).mark_line().encode(
                x=alt.X("date:T", title=""),
                y=alt.Y("change_pct:Q", title=f"{label} % from Jan 1"),
                tooltip=[alt.Tooltip("date:T"), alt.Tooltip("change_pct:Q", format=".1f")]
            ).properties(height=180)
            st.altair_chart(ch, use_container_width=True)
        else:
            st.info(f"No {label} history (rate limit?)")

st.markdown("<hr/>", unsafe_allow_html=True)

# --------------------
# On-chain Liquidity & TVL (cards)
# --------------------
st.subheader("On-chain Liquidity & TVL")
colA, colB = st.columns([1,2])

with colA:
    total, sc_raw = defillama_stablecoins_total()
    st.markdown(f"<div class='card'><div class='caption'>Stablecoin Market Cap (approx)</div><div class='metric'>{usd_big(total)}</div><div class='caption'>Source: DeFiLlama (global)</div></div>", unsafe_allow_html=True)

with colB:
    tvl_df = llama_solana_tvl()
    if not tvl_df.empty:
        ch = alt.Chart(tvl_df).mark_line().encode(
            x=alt.X("date:T", title=""),
            y=alt.Y("tvl:Q", title="Solana TVL (USD)"),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("tvl:Q", format=",.0f")]
        ).properties(height=260)
        st.altair_chart(ch, use_container_width=True)
        st.caption(f"Latest TVL: ${float(tvl_df.iloc[-1]['tvl']):,.0f}")
    else:
        st.info("No TVL data returned (try again)")

st.markdown("<hr/>", unsafe_allow_html=True)

# --------------------
# Global Crypto Context
# --------------------
st.subheader("Global Crypto Context")
@st.cache_data(ttl=3600)
def cg_global():
    return http_json("https://api.coingecko.com/api/v3/global") or {}
g = cg_global().get("data", {})
total_mc = (g.get("total_market_cap", {}) or {}).get("usd")
btc_pct = (g.get("market_cap_percentage", {}) or {}).get("btc")
btc_cap = total_mc * (btc_pct/100.0) if total_mc and btc_pct is not None else None
alt_cap = (total_mc - btc_cap) if (total_mc and btc_cap) else None

d1,d2,d3 = st.columns(3)
with d1: st.markdown(f"<div class='card'><div class='caption'>BTC Dominance</div><div class='metric'>{'—' if btc_pct is None else f'{btc_pct:.1f}%'}</div></div>", unsafe_allow_html=True)
with d2: st.markdown(f"<div class='card'><div class='caption'>TOTAL Market Cap</div><div class='metric'>{usd_big(total_mc)}</div></div>", unsafe_allow_html=True)
with d3: st.markdown(f"<div class='card'><div class='caption'>Altcoin Market Cap</div><div class='metric'>{usd_big(alt_cap)}</div></div>", unsafe_allow_html=True)

# --------------------
# Notes
# --------------------
st.markdown("<hr/>", unsafe_allow_html=True)
st.caption("Tip: if any CoinGecko panel errors with 429 (rate limit), increase the auto-refresh interval or try again later.")

