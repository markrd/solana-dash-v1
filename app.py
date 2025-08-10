import os, time
from pathlib import Path
from typing import Optional, Tuple
import requests
import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta
# ---------- Modern UI pack (paste near top of app.py) ----------
MODERN_STYLE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0b0f19; --bg2:#0f1322; --card:#0f1322; --text:#eaf0f6; --muted:#9aa3ab; --border:rgba(255,255,255,.065);
  --accent:#22d3ee; --accent-2:#93c5fd; --up:#2dd4bf; --down:#fb7185;
}
html,body,[class^="css"]{font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,"Helvetica Neue",Arial,"Noto Sans","Apple Color Emoji","Segoe UI Emoji","Segoe UI Symbol";}

.block-container{padding-top: 1rem; max-width: 1200px;}
/* Cards */
.card{background:var(--card); border:1px solid var(--border); border-radius:16px; padding:16px 18px;}
.card + .card{margin-top: 10px;}
.kpi{display:flex; flex-direction:column; gap:6px;}
.kpi .label{color:var(--muted); font-size:.85rem;}
.kpi .value{font-weight:700; font-size:1.75rem; line-height:1.1}
.kpi .sub{color:var(--muted); font-size:.85rem;}
.kpi .delta.up{color:var(--up); font-weight:600}
.kpi .delta.down{color:var(--down); font-weight:600}

/* Pills & chips */
.pills{display:flex; gap:8px; flex-wrap: wrap;}
.pill{background:transparent; border:1px solid var(--border); color:var(--text); padding:6px 10px; border-radius:999px; font-size:.85rem}
.pill.active{background: rgba(34,211,238,.12); border-color: var(--accent);}

/* Section header */
.sec{display:flex; justify-content:space-between; align-items:center; margin:6px 0 10px}
.sec h3{margin:0; font-size:1.1rem}
.sec .hint{color:var(--muted); font-size:.9rem}

/* Hero */
.hero{display:flex; align-items:flex-end; justify-content:space-between; gap:10px; margin-bottom: 10px}
.hero h1{margin:0; font-size:2.1rem}
.badge{display:inline-block; margin-left:8px; background:rgba(34,211,238,.12); border:1px solid var(--accent);
  color:var(--accent); padding:2px 8px; border-radius:999px; font-size:.85rem}

/* Make charts feel integrated */
.vega-embed, .element-container:has(canvas){background:var(--card); border:1px solid var(--border); border-radius:16px; padding:8px}
</style>
"""

def ui_hero(title:str, subtitle:str="", badge:str=""):
    b = f'<span class="badge">{badge}</span>' if badge else ""
    return f'''<div class="hero"><div>
        <h1>{title} {b}</h1>
        <div class="hint">{subtitle}</div>
    </div></div>'''

def ui_kpi(label:str, value:str, sub:str="", delta:float|None=None):
    d_html=""
    if delta is not None:
        cls = "up" if delta>=0 else "down"
        d_html = f'<span class="delta {cls}">{delta:+.2f}%</span>'
    return f'''
    <div class="card kpi">
      <div class="label">{label}</div>
      <div class="value">{value}</div>
      <div class="sub">{d_html} {sub}</div>
    </div>'''

def ui_section(title:str, right_hint:str=""):
    return f'''<div class="sec"><h3>{title}</h3><div class="hint">{right_hint}</div></div>'''
# ---------- /Modern UI pack ----------

# --- Ensure Streamlit can write configs (avoids '/.streamlit' issues) ---
try:
    Path(".streamlit").mkdir(exist_ok=True)
    os.environ.setdefault("HOME", str(Path.cwd()))
    os.environ.setdefault("STREAMLIT_GLOBAL_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
    os.environ.setdefault("STREAMLIT_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
except Exception:
    pass

st.set_page_config(page_title="Solana Dashboard — v2.2.1 (fast)", layout="wide")
st.markdown(MODERN_STYLE, unsafe_allow_html=True)

# ---------------------------------
# Global HTTP helper (cached + fast-fail backoff)
# ---------------------------------
DEFAULT_HEADERS = {"User-Agent": "solana-dash/1.0 (+streamlit)"}
HTTP_TIMEOUT = 6     # ↓ from 12
HTTP_TRIES   = 2     # ↓ from 3

@st.cache_data(ttl=600)
def http_json(url: str, params=None, tries: int = HTTP_TRIES, timeout: int = HTTP_TIMEOUT):
    last = None
    for i in range(tries):
        r = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
        if (r.status_code == 429 or 500 <= r.status_code < 600) and i < tries - 1:
            time.sleep(1.2 * (2 ** i))
            last = f"{r.status_code} {r.text[:200]}"
            continue
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return None
    raise requests.HTTPError(f"GET {url} failed after {tries} tries. Last: {last}")

# ---------------------------
# CoinGecko helpers
# ---------------------------
@st.cache_data(ttl=60)
def cg_simple_prices(ids_csv: str):
    try:
        js = http_json(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ids_csv, "vs_currencies": "usd", "include_24hr_change": "true"},
        )
        return js or {}
    except Exception:
        return {}

@st.cache_data(ttl=600)
def cg_market_chart(coin_id: str, days: int = 90, vs="usd") -> pd.DataFrame:
    try:
        js = http_json(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
            params={"vs_currency": vs, "days": days},
        )
    except Exception:
        return pd.DataFrame(columns=["date", "price"])
    prices = (js or {}).get("prices", [])
    if not prices:
        return pd.DataFrame(columns=["date", "price"])
    df = pd.DataFrame(prices, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms", errors="coerce")
    df = df.dropna(subset=["date"])
    return df[["date", "price"]]

@st.cache_data(ttl=900)
def cg_market_caps_sum(ids=("tether", "usd-coin", "dai"), days=180) -> pd.DataFrame:
    out = None
    for coin in ids:
        try:
            js = http_json(
                f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
                params={"vs_currency": "usd", "days": days},
            )
        except Exception:
            continue
        caps = (js or {}).get("market_caps", [])
        if not caps:
            continue
        df = pd.DataFrame(caps, columns=["ts", f"cap_{coin}"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms", errors="coerce")
        df = df.dropna(subset=["date"])[["date", f"cap_{coin}"]]
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
    out = out[["date", "total_cap"]].dropna()
    return out

# ---------------------------
# DeFiLlama helpers
# ---------------------------
@st.cache_data(ttl=900)
def llama_solana_tvl() -> pd.DataFrame:
    try:
        js = http_json("https://api.llama.fi/v2/historicalChainTvl/Solana")
    except Exception:
        return pd.DataFrame(columns=["date", "tvl"])
    if not js:
        return pd.DataFrame(columns=["date", "tvl"])
    df = pd.DataFrame(js)
    if "date" not in df or "tvl" not in df:
        return pd.DataFrame(columns=["date", "tvl"])
    df["date"] = pd.to_datetime(df["date"], unit="s", errors="coerce")
    df = df.dropna(subset=["date"])
    return df[["date", "tvl"]]

@st.cache_data(ttl=900)
def defillama_stablecoins_total() -> Tuple[Optional[float], dict]:
    try:
        js = http_json("https://stablecoins.llama.fi/stablecoins")
    except Exception:
        return None, {}
    total = None
    if isinstance(js, dict):
        for key in ("total", "totalCirculatingUSD", "totalCirculating"):
            val = js.get(key)
            if isinstance(val, (int, float)):
                total = float(val)
                break
    return total, (js or {})

@st.cache_data(ttl=600)
def cg_stablecoins_total_fallback() -> Tuple[Optional[float], dict]:
    try:
        js = http_json(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "tether,usd-coin,dai", "vs_currencies": "usd", "include_market_cap": "true"},
        )
    except Exception:
        return None, {}
    def _cap(name):
        try:
            return float(js.get(name, {}).get("usd_market_cap"))
        except Exception:
            return None
    parts = [_cap("tether"), _cap("usd-coin"), _cap("dai")]
    parts = [p for p in parts if p is not None]
    return (sum(parts) if parts else None), (js or {})

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
# Sidebar
# ---------------------------
with st.sidebar:
    st.header("Controls")
    auto = st.checkbox("Auto-refresh", value=False)
    interval = st.slider("Interval (sec)", 15, 180, 60, 5)
    fast_mode = st.toggle("Fast mode (recommended)", value=True, help="Load heavy data only when toggled OFF.")
    debug = st.toggle("Debug", value=False, help="Show raw API shapes and DataFrame sizes")
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
st.title("Solana Dashboard — v2.2.1 (fast)")
st.caption(f"Local time (UTC+4): {datetime.now(timezone(timedelta(hours=4))).strftime('%Y-%m-%d %H:%M:%S')}")

with st.spinner("Loading prices…"):
    data = cg_simple_prices("solana,ethereum,bitcoin")
sol, eth, btc = data.get("solana", {}), data.get("ethereum", {}), data.get("bitcoin", {})
c1, c2, c3 = st.columns(3)
with c1: st.metric("SOL (USD)", sol.get("usd", "—"), pct(sol.get("usd_24h_change", 0)))
with c2: st.metric("ETH (USD)", eth.get("usd", "—"), pct(eth.get("usd_24h_change", 0)))
with c3: st.metric("BTC (USD)", btc.get("usd", "—"), pct(btc.get("usd_24h_change", 0)))

st.divider()

# ---------------------------
# Charts — 90-day prices
# ---------------------------
st.subheader("90-Day Price Charts")
coin_map = {"Solana (SOL)": "solana", "Ethereum (ETH)": "ethereum", "Bitcoin (BTC)": "bitcoin"}
left, right = st.columns([1, 3])

with left:
    choice = st.radio("Select asset", list(coin_map.keys()), index=0)
    st.caption("Data: CoinGecko /market_chart")

with right:
    cid = coin_map[choice]
    with st.spinner(f"Loading {cid.upper()} chart…"):
        df = cg_market_chart(cid, days=90)
    if debug: st.info(f"{cid} chart df shape: {df.shape}")
    if df.empty:
        st.info("No chart data (rate limited?). Try again later.")
    else:
        base = float(df.iloc[0]["price"])
        df["pct_from_start"] = (df["price"] / base - 1) * 100.0
        tab1, tab2 = st.tabs(["Price (USD)", "% from start"])
        with tab1:
            st.altair_chart(line_chart(df, "date", "price", "Price (USD)", 300), use_container_width=True)
        with tab2:
            st.altair_chart(line_chart(df, "date", "pct_from_start", "% since first point", 300), use_container_width=True)

st.divider()

# ---------------------------
# On-chain Liquidity & TVL (deferred if Fast mode)
# ---------------------------
st.subheader("On-chain Liquidity & TVL")
if fast_mode:
    st.info("Fast mode is ON — skipping heavy data to keep the app snappy. Toggle it OFF to load TVL and Stablecoins.")
else:
    mcol, gcol = st.columns([1, 2])

    with mcol:
        # Solana TVL
        with st.spinner("Loading Solana TVL…"):
            tvl_df = llama_solana_tvl()
        if debug: st.info(f"TVL df shape: {tvl_df.shape}")
        if tvl_df.empty:
            st.info("TVL unavailable right now.")
        else:
            tvl_30d = pct_change_over_days(tvl_df.rename(columns={"tvl": "value"}), "value", 30)
            latest_tvl = float(tvl_df.iloc[-1]["tvl"])
            st.metric("Solana TVL (latest)", f"${latest_tvl:,.0f}", pct(tvl_30d) if tvl_30d is not None else None)

        # Stablecoins (metric + delta)
        with st.spinner("Loading Stablecoin liquidity…"):
            total_dl, raw_dl = defillama_stablecoins_total()
            sc_df = cg_market_caps_sum(days=180)
        if debug:
            st.info(f"Stablecoin df shape: {sc_df.shape}")
            with st.expander("Raw DeFiLlama stablecoins JSON (first 800 chars)"):
                st.code(str(raw_dl)[:800])

        sc_delta = pct_change_over_days(sc_df.rename(columns={"total_cap": "value"}), "value", 30) if not sc_df.empty else None
        used_source = "DeFiLlama total"
        total_display = total_dl
        if total_display is None:
            total_cg, raw_cg = cg_stablecoins_total_fallback()
            total_display = total_cg
            used_source = "CoinGecko (USDT+USDC+DAI)"
            if debug:
                with st.expander("Raw CoinGecko fallback JSON (first 400 chars)"):
                    st.code(str(raw_cg)[:400])

        st.metric("Stablecoin Liquidity (approx.)", usd_big(total_display), pct(sc_delta) if sc_delta is not None else None)
        st.caption(f"Source: {used_source}. Chart uses USDT+USDC+DAI sum as a liquidity proxy.")

    with gcol:
        tabs = st.tabs(["Solana TVL (365d)", "Stablecoin Liquidity (180d)"])
        with tabs[0]:
            if tvl_df.empty:
                st.info("No TVL history to display.")
            else:
                st.altair_chart(line_chart(tvl_df, "date", "tvl", "Solana TVL (USD)", 320), use_container_width=True)
        with tabs[1]:
            if sc_df.empty:
                st.info("No stablecoin history (rate limited?).")
            else:
                st.altair_chart(line_chart(sc_df, "date", "total_cap", "USDT+USDC+DAI Market Cap (USD)", 320), use_container_width=True)

st.divider()

# ---------------------------
# SOL vs ETH — Relative Strength (deferred if Fast mode)
# ---------------------------
st.subheader("SOL vs ETH — Relative Strength")
if fast_mode:
    st.info("Fast mode is ON — toggle OFF to compute SOL/ETH relative strength.")
else:
    def sol_eth_ratio(days=365) -> pd.DataFrame:
        sol = cg_market_chart("solana", days=days)
        eth = cg_market_chart("ethereum", days=days)
        if sol.empty or eth.empty:
            return pd.DataFrame(columns=["date","sol_eth_ratio"])
        merged = pd.merge_asof(
            sol.sort_values("date"),
            eth.sort_values("date"),
            on="date",
            direction="nearest",
            tolerance=pd.Timedelta("1H"),
            suffixes=("_sol","_eth")
        ).dropna()
        merged["sol_eth_ratio"] = merged["price_sol"] / merged["price_eth"]
        return merged[["date","sol_eth_ratio"]]

    with st.spinner("Loading SOL/ETH…"):
        rs = sol_eth_ratio(365)
    if debug: st.info(f"SOL/ETH ratio df shape: {rs.shape}")
    if rs.empty:
        st.info("No ratio data (rate limited?).")
    else:
        st.altair_chart(line_chart(rs, "date", "sol_eth_ratio", "SOL/ETH", 320), use_container_width=True)
        latest = float(rs.iloc[-1]["sol_eth_ratio"])
        ma30 = float(rs["sol_eth_ratio"].tail(30).mean())
        st.caption(f"Latest ratio: {latest:.4f} | 30-day avg: {ma30:.4f}")

st.caption("Tip: if you see 429 errors from CoinGecko, slow the refresh interval or keep Fast mode ON.")




