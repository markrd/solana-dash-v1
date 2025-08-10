import os, time
from pathlib import Path
from typing import Optional, Tuple
import requests
import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timezone, timedelta

# --- Ensure Streamlit can write configs (avoids '/.streamlit' issues) ---
try:
    Path(".streamlit").mkdir(exist_ok=True)
    os.environ.setdefault("HOME", str(Path.cwd()))
    os.environ.setdefault("STREAMLIT_GLOBAL_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
    os.environ.setdefault("STREAMLIT_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
except Exception:
    pass

st.set_page_config(page_title="Solana Dashboard — v2.2", layout="wide")

# ---------------------------------
# Global HTTP helper (cached+backoff)
# ---------------------------------
DEFAULT_HEADERS = {"User-Agent": "solana-dash/1.0 (+https://streamlit.app)"}

@st.cache_data(ttl=600)
def http_json(url: str, params=None, tries: int = 3, timeout: int = 12):
    last = None
    for i in range(tries):
        r = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
        if (r.status_code == 429 or 500 <= r.status_code < 600) and i < tries - 1:
            time.sleep(1.5 * (2 ** i))
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

@st.cache_data(ttl=1800)
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
@st.cache_data(ttl=1200)
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

@st.cache_data(ttl=1200)
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
    st.header("Refresh")
    auto = st.checkbox("Auto-refresh", value=False)
    interval = st.slider("Interval (sec)", 15, 180, 60, 5)
    debug = st.toggle("Debug mode", value=False, help="Show raw API shapes and DataFrame sizes")
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
st.title("Solana Dashboard — v2.2")
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
coin_map = {"Solana (SOL)": "solana", "Ethereum (ETH)": "ethereum", "Bitcoin (BTC)": "bitcoin"}
left, right = st.columns([1, 3])

with left:
    choice = st.radio("Select asset", list(coin_map.keys()), index=0)
    st.caption("Data: CoinGecko /market_chart")

with right:
    cid = coin_map[choice]
    try:
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
    except Exception as e:
        st.error(f"Chart error: {e}")

st.divider()

# ---------------------------
# On-chain Liquidity & TVL
# ---------------------------
st.subheader("On-chain Liquidity & TVL")

mcol, gcol = st.columns([1, 2])

with mcol:
    # Solana TVL
    try:
        tvl_df = llama_solana_tvl()
        if debug: st.info(f"TVL df shape: {tvl_df.shape}")
        if tvl_df.empty:
            st.info("TVL unavailable right now.")
        else:
            tvl_30d = pct_change_over_days(tvl_df.rename(columns={"tvl": "value"}), "value", 30)
            latest_tvl = float(tvl_df.iloc[-1]["tvl"])
            st.metric("Solana TVL (latest)", f"${latest_tvl:,.0f}", pct(tvl_30d) if tvl_30d is not None else None)
    except Exception as e:
        st.error(f"TVL error: {e}")

    # Stablecoins (metric + delta)
    try:
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
    except Exception as e:
        st.error(f"Stablecoin liquidity error: {e}")

with gcol:
    tabs = st.tabs(["Solana TVL (365d)", "Stablecoin Liquidity (180d)"])
    with tabs[0]:
        try:
            tvl_df = llama_solana_tvl()
            if tvl_df.empty:
                st.info("No TVL history to display.")
            else:
                st.altair_chart(line_chart(tvl_df, "date", "tvl", "Solana TVL (USD)", 320), use_container_width=True)
        except Exception as e:
            st.error(f"TVL chart error: {e}")
    with tabs[1]:
        try:
            sc_df = cg_market_caps_sum(days=180)
            if sc_df.empty:
                st.info("No stablecoin history (rate limited?).")
            else:
                st.altair_chart(line_chart(sc_df, "date", "total_cap", "USDT+USDC+DAI Market Cap (USD)", 320), use_container_width=True)
        except Exception as e:
            st.error(f"Stablecoin chart error: {e}")

st.divider()

# ---------------------------
# NEW: SOL vs ETH — Relative Strength (365d)
# ---------------------------
st.subheader("SOL vs ETH — Relative Strength")

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

try:
    rs = sol_eth_ratio(365)
    if debug: st.info(f"SOL/ETH ratio df shape: {rs.shape}")
    if rs.empty:
        st.info("No ratio data (rate limited?).")
    else:
        ch = line_chart(rs, "date", "sol_eth_ratio", "SOL/ETH", 320)
        st.altair_chart(ch, use_container_width=True)
        latest = float(rs.iloc[-1]["sol_eth_ratio"])
        ma30 = float(rs["sol_eth_ratio"].tail(30).mean())
        st.caption(f"Latest ratio: {latest:.4f} | 30-day avg: {ma30:.4f}")
except Exception as e:
    st.error(f"SOL/ETH ratio error: {e}")

st.divider()

# ---------------------------
# NEW: 30-Day Signals + Bullishness + Alerts
# ---------------------------
st.subheader("30-Day Trend Signals")

def pct_to_score(pct, pos=20.0, neg=-20.0):
    if pct is None: return None
    denom = pos if pct >= 0 else abs(neg)
    score = 50 + (pct/denom)*50
    return max(0, min(100, score))

# TVL 30d
try:
    tvl_30d = pct_change_over_days(llama_solana_tvl().rename(columns={"tvl":"value"}), "value", 30)
except Exception:
    tvl_30d = None

# Stablecoins 30d
try:
    sc_df = cg_market_caps_sum(days=90)
    stable_30d = pct_change_over_days(sc_df.rename(columns={"total_cap":"value"}), "value", 30)
except Exception:
    stable_30d = None

# SOL/ETH vs 30d avg → % deviation
rel_30d = None
try:
    rs = sol_eth_ratio(60)
    if not rs.empty:
        latest_ratio = float(rs.iloc[-1]["sol_eth_ratio"])
        ma30 = float(rs["sol_eth_ratio"].tail(30).mean())
        if ma30 != 0:
            rel_30d = (latest_ratio/ma30 - 1) * 100.0
except Exception:
    pass

c1, c2, c3 = st.columns(3)
with c1: st.metric("TVL 30d", "—" if tvl_30d is None else f"{tvl_30d:.1f}%")
with c2: st.metric("Stablecoins 30d", "—" if stable_30d is None else f"{stable_30d:.1f}%")
with c3: st.metric("SOL/ETH vs 30d avg", "—" if rel_30d is None else f"{rel_30d:.1f}%")

st.subheader("Bullishness Score")
score_tvl     = pct_to_score(tvl_30d)
score_stables = pct_to_score(stable_30d)
score_rel     = pct_to_score(rel_30d, 15.0, -15.0)  # tighter band for ratio

weights = {"tvl": 0.40, "stables": 0.35, "rel": 0.25}
num = sum(s*w for s, w in [(score_tvl, weights["tvl"]), (score_stables, weights["stables"]), (score_rel, weights["rel"])] if s is not None)
den = sum(w for s, w in [(score_tvl, weights["tvl"]), (score_stables, weights["stables"]), (score_rel, weights["rel"])] if s is not None)
score = (num/den) if den > 0 else None

prev = st.session_state.get("last_bull_score")
if score is None:
    st.info("Not enough data yet to compute a score.")
else:
    delta = None if prev is None else score - prev
    st.session_state["last_bull_score"] = score
    light = "🟢" if score >= 70 else ("🟠" if score >= 40 else "🔴")
    x1, x2 = st.columns([1,1])
    with x1:
        st.markdown(f"### {score:.0f} / 100")
        if delta is not None:
            st.caption(f"Δ since last view: {delta:+.0f}")
    with x2:
        st.markdown(f"### Signal: {light}")

# Alerts
st.subheader("Alerts")
alerts = []
def add_alert(ok, msg):
    if ok: alerts.append(msg)

add_alert(tvl_30d is not None and tvl_30d <= -10, f"Solana TVL 30d is {tvl_30d:.1f}% (≤ -10%).")
add_alert(stable_30d is not None and stable_30d <= -10, f"Stablecoin cap proxy 30d is {stable_30d:.1f}% (≤ -10%).")
add_alert(rel_30d is not None and rel_30d <= -5, f"SOL/ETH vs 30d avg is {rel_30d:.1f}% (≤ -5%).")

if alerts:
    st.warning("⚠️ Risk flags:\n\n- " + "\n- ".join(alerts))
else:
    st.success("✅ No major risk flags right now.")

st.caption("Tip: if you see 429 errors from CoinGecko, slow the refresh interval or try again later.")



