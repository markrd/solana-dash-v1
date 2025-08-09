import os
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

import requests
import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from streamlit_autorefresh import st_autorefresh
import feedparser

st.set_page_config(page_title="Solana Dashboard v1", layout="wide")
# Auto-refresh controls
with st.sidebar:
    st.markdown("### Refresh")
    auto = st.checkbox("Auto-refresh", value=False, help="Enable periodic refresh")
    interval = st.slider("Interval (seconds)", 10, 120, 30, 5)
    if not auto and st.button("🔄 Refresh now"):
        st.cache_data.clear()
        st.rerun()

# Trigger refresh if enabled
if 'auto_tick' not in st.session_state:
    st.session_state['auto_tick'] = 0

if auto:
    st_autorefresh(interval=interval * 1000, key="auto_refresh_tick")


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

st.title("Solana Dashboard")
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

st.subheader("Macro: PMI & M2")
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
# --------------------
# Risk & Liquidity (FRED)
# --------------------
st.subheader("Risk & Liquidity (FRED)")
if not FRED_API_KEY:
    st.info("Add FRED_API_KEY in app Secrets to enable this panel.")
else:
    # Helper to get latest numeric value from a fred_series() DataFrame
    def _latest(df):
        try:
            return None if df is None or df.empty else float(df.dropna().iloc[-1]["value"])
        except Exception:
            return None

    # Pull series
    dgs10 = fred_series("DGS10", observation_start="2015-01-01")   # 10-year Treasury
    dgs2  = fred_series("DGS2",  observation_start="2015-01-01")   # 2-year Treasury
    vix   = fred_series("VIXCLS", observation_start="2015-01-01")  # VIX
    walcl = fred_series("WALCL",  observation_start="2015-01-01")  # Fed balance sheet (millions USD)

    # Latest values
    t10 = _latest(dgs10)
    t2  = _latest(dgs2)
    vix_v = _latest(vix)
    walcl_v = _latest(walcl)  # millions of USD

    # Compute 10y-2y spread
    yc_spread = None
    if t10 is not None and t2 is not None:
        yc_spread = t10 - t2

    # Formatters
    def _fmt_spread(x):
        return "—" if x is None else f"{x:.2f}"

    def _fmt_vix(x):
        return "—" if x is None else f"{x:.1f}"

    def _fmt_trillions_from_millions(x):
        return "—" if x is None else f"${x/1_000_000:.2f}T"

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Yield Curve (10y−2y, %)", _fmt_spread(yc_spread), help="> 0 = normal; < 0 = inverted")
    with c2:
        st.metric("VIX (implied vol)", _fmt_vix(vix_v))
    with c3:
        st.metric("Fed Balance Sheet (WALCL)", _fmt_trillions_from_millions(walcl_v))






# --------------------
# SOL / ETH (Relative Strength)
# --------------------
st.subheader("SOL vs ETH — Relative Strength")

@st.cache_data(ttl=3600)
def cg_market_chart(coin_id: str, days: int = 365):
    """CoinGecko market_chart prices (USD). Returns DataFrame with 'date' and 'price'."""
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    r = requests.get(url, params={"vs_currency": "usd", "days": days}, timeout=10)
    r.raise_for_status()
    js = r.json()
    prices = js.get("prices", [])
    if not prices:
        return pd.DataFrame(columns=["date", "price"])
    df = pd.DataFrame(prices, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df[["date", "price"]]

try:
    sol_df = cg_market_chart("solana", days=365)
    eth_df = cg_market_chart("ethereum", days=365)
    if sol_df.empty or eth_df.empty:
        st.warning("Couldn’t load history from CoinGecko (rate limit?). Try refresh.")
    else:
        merged = pd.merge_asof(
            sol_df.sort_values("date"), 
            eth_df.sort_values("date"),
            on="date", direction="nearest", tolerance=pd.Timedelta("1H"),
            suffixes=("_sol", "_eth")
        ).dropna()
        merged["sol_eth_ratio"] = merged["price_sol"] / merged["price_eth"]
        st.line_chart(merged.set_index("date")["sol_eth_ratio"])
        latest = merged.iloc[-1]["sol_eth_ratio"]
        ma30 = merged["sol_eth_ratio"].tail(30).mean()
        st.caption(f"Latest ratio: {latest:.4f} | 30-day avg: {ma30:.4f}")
except Exception as e:
    st.error(f"SOL/ETH ratio error: {e}")
# --------------------
# Global Crypto Context
# --------------------
st.subheader("Global Crypto Context")

@st.cache_data(ttl=600)
def cg_global():
    r = requests.get("https://api.coingecko.com/api/v3/global", timeout=8)
    r.raise_for_status()
    return r.json()

def fmt_large_usd(x):
    if x is None:
        return "—"
    try:
        x = float(x)
        if x >= 1e12:
            return f"${x/1e12:,.2f}T"
        if x >= 1e9:
            return f"${x/1e9:,.1f}B"
        if x >= 1e6:
            return f"${x/1e6:,.0f}M"
        return f"${x:,.0f}"
    except Exception:
        return "—"

try:
    g = cg_global().get("data", {})
    total = g.get("total_market_cap", {}).get("usd")
    btc_pct = g.get("market_cap_percentage", {}).get("btc")  # percent

    btc_cap = total * (btc_pct / 100.0) if (total is not None and btc_pct is not None) else None
    alt_cap = (total - btc_cap) if (total is not None and btc_cap is not None) else None

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("BTC Dominance", f"{btc_pct:.1f}%" if btc_pct is not None else "—")
    with c2:
        st.metric("TOTAL Market Cap", fmt_large_usd(total))
    with c3:
        st.metric("Altcoin Market Cap", fmt_large_usd(alt_cap))

    st.caption("Source: CoinGecko /global")
except Exception as e:
    st.error(f"Global context error: {e}")



# --------------------
# 30-Day Trend Signals (TVL, Stablecoins, SOL/ETH)
# --------------------
st.subheader("30-Day Trend Signals")

def pct_change_over_30d(df, col):
    """Percent change over ~30 days using the first point within last 30d as baseline."""
    if df is None or df.empty or col not in df:
        return None
    df = df[["date", col]].dropna()
    if df.empty:
        return None
    latest = df["date"].max()
    base_df = df[df["date"] >= latest - pd.Timedelta(days=30)]
    base_val = float(base_df.iloc[0][col]) if not base_df.empty else float(df.iloc[0][col])
    last_val = float(df.iloc[-1][col])
    if base_val == 0:
        return None
    return (last_val - base_val) / base_val * 100.0

def light(p):
    if p is None:
        return "⚪"
    if p >= 5:
        return "🟢"
    if p <= -5:
        return "🔴"
    return "🟠"

# TVL 30d change
try:
    tvl_df = solana_tvl_series().rename(columns={"tvl": "value"})
    tvl_30d = pct_change_over_30d(tvl_df, "value")
except Exception:
    tvl_30d = None

@st.cache_data(ttl=3600)
def cg_caps(coin_id, days=90):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    r = requests.get(url, params={"vs_currency": "usd", "days": days}, timeout=10)
    r.raise_for_status()
    js = r.json()
    caps = js.get("market_caps", [])
    if not caps:
        return pd.DataFrame(columns=["date", "cap"])
    df = pd.DataFrame(caps, columns=["ts", "cap"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df[["date", "cap"]]

# Stablecoin liquidity proxy = USDT + USDC + DAI
try:
    usdt = cg_caps("tether")
    usdc = cg_caps("usd-coin")
    dai  = cg_caps("dai")
    sc = pd.merge_asof(
        usdt.sort_values("date"),
        usdc.sort_values("date"),
        on="date", direction="nearest",
        tolerance=pd.Timedelta("1H"),
        suffixes=("_usdt", "_usdc")
    ).dropna()
    sc = pd.merge_asof(
        sc.sort_values("date"),
        dai.sort_values("date"),
        on="date", direction="nearest",
        tolerance=pd.Timedelta("1H")
    )
    sc["total"] = sc[["cap_usdt", "cap_usdc", "cap"]].sum(axis=1)  # 'cap' is DAI
    stable_30d = pct_change_over_30d(sc.rename(columns={"total": "value"}), "value")
except Exception:
    stable_30d = None

# SOL/ETH vs 30d average (requires 'merged' from the relative-strength block)
try:
    if "merged" in locals() and not merged.empty:
        latest_ratio = float(merged.iloc[-1]["sol_eth_ratio"])
        ma30 = float(merged["sol_eth_ratio"].tail(30).mean())
        rel_30d = (latest_ratio / ma30 - 1) * 100.0 if ma30 != 0 else None
    else:
        rel_30d = None
except Exception:
    rel_30d = None

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("TVL 30d change", f"{tvl_30d:.1f}%" if tvl_30d is not None else "—")
    st.write(light(tvl_30d))
with c2:
    st.metric("Stablecoins 30d (USDT+USDC+DAI)", f"{stable_30d:.1f}%" if stable_30d is not None else "—")
    st.write(light(stable_30d))
with c3:
    st.metric("SOL/ETH vs 30d avg", f"{rel_30d:.1f}%" if rel_30d is not None else "—")
    st.write(light(rel_30d))
# --------------------
# Bullishness Score (0–100)
# --------------------
st.subheader("Bullishness Score")

# Map % change → 0..100 (−20% → 0, 0% → 50, +20% → 100). Clamp to [0,100].
def pct_to_score(pct, pos=20.0, neg=-20.0):
    if pct is None:
        return None
    denom = pos if pct >= 0 else abs(neg)
    score = 50 + (pct / denom) * 50
    return max(0, min(100, score))

# Convert each signal to a score
score_tvl     = pct_to_score(tvl_30d)                # TVL 30d %
score_stables = pct_to_score(stable_30d)             # USDT+USDC+DAI 30d %
score_rel     = pct_to_score(rel_30d, 15.0, -15.0)   # SOL/ETH vs 30d avg (narrower band)

# Weights (tweak if you like)
weights = {"tvl": 0.40, "stables": 0.35, "rel": 0.25}

components = [
    ("TVL",     score_tvl,     weights["tvl"]),
    ("Stables", score_stables, weights["stables"]),
    ("SOL/ETH", score_rel,     weights["rel"]),
]

# Weighted average over available components
num, den = 0.0, 0.0
for _, s, w in components:
    if s is not None:
        num += s * w
        den += w
score = (num / den) if den > 0 else None

# Store last score for delta
prev = st.session_state.get("last_bull_score")

if score is None:
    st.info("Not enough data yet to compute a score.")
else:
    delta = None if prev is None else score - prev
    st.session_state["last_bull_score"] = score

    # Traffic light
    light = "🟢" if score >= 70 else ("🟠" if score >= 40 else "🔴")

    c1, c2 = st.columns([1,1])
    with c1:
        st.metric("Bullishness (0–100)", f"{score:.0f}", (f"{delta:+.0f}" if delta is not None else None))
    with c2:
        st.write(f"Signal: {light}")

    # Small breakdown
    lines = []
    for name, s, _ in components:
        lines.append(f"- {name}: " + ("—" if s is None else f"{s:.0f}"))
    st.caption("Components\n" + "\n".join(lines))

# --------------------
# Alerts — key risk flags
# --------------------
st.subheader("Alerts")

alerts = []

def add_alert(ok, msg):
    if ok:
        alerts.append(msg)

# Thresholds (tweak to taste)
add_alert(tvl_30d is not None and tvl_30d <= -10, f"Solana TVL 30d is {tvl_30d:.1f}% (≤ -10%).")
add_alert(stable_30d is not None and stable_30d <= -10, f"Stablecoin cap 30d is {stable_30d:.1f}% (≤ -10%).")
add_alert(rel_30d is not None and rel_30d <= -5, f"SOL/ETH vs 30d avg is {rel_30d:.1f}% (≤ -5%).")
add_alert('vix_v' in locals() and vix_v is not None and vix_v >= 25, f"VIX is {vix_v:.1f} (≥ 25).")
add_alert('yc_spread' in locals() and yc_spread is not None and yc_spread < 0, f"Yield curve is inverted ({yc_spread:.2f}%).")

if alerts:
    st.warning("⚠️ Risk flags active:\n\n- " + "\n- ".join(alerts))
else:
    st.success("✅ No major risk flags right now.")






# --------------------
# Explain this dashboard (GPT)
# --------------------
from openai import OpenAI
import json

def _to_num(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None

@st.cache_data(ttl=30)
def build_dashboard_snapshot():
    """Gather key metrics from the current session for the explainer."""
    snap = {
        "timestamp_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "prices": {
            "sol_usd": _to_num(data.get("solana", {}).get("usd")) if "data" in locals() else None,
            "eth_usd": _to_num(data.get("ethereum", {}).get("usd")) if "data" in locals() else None,
        },
        "tvl": {
            "latest": None if 'df' not in locals() or df.empty else _to_num(df.iloc[-1]["tvl"]),
            "chg_30d_pct": _to_num(tvl_30d),
        },
        "stablecoins": {
            "chg_30d_pct": _to_num(stable_30d),
        },
        "relative_strength": {
            "sol_eth_ratio_latest": None if 'merged' not in locals() or merged.empty else _to_num(merged.iloc[-1]["sol_eth_ratio"]),
            "vs_30d_avg_pct": _to_num(rel_30d),
        },
        "macro": {
            "yield_curve_10y_minus_2y": _to_num(yc_spread) if 'yc_spread' in locals() else None,
            "vix": _to_num(vix_v) if 'vix_v' in locals() else None,
            "fed_balance_sheet_trillions": None if 'walcl_v' not in locals() or walcl_v is None else float(walcl_v)/1_000_000,
        },
        "bullishness_score": _to_num(st.session_state.get("last_bull_score")),
    }
    return snap

st.divider()
st.subheader("🧠 Explain this dashboard")

with st.expander("Ask GPT for a quick read (keeps your data local to this app)"):
    tone = st.selectbox("Tone", ["Concise bullets", "Narrative summary", "Risk-focused", "Beginner-friendly"])
    if st.button("Generate commentary"):
        try:
            snapshot = build_dashboard_snapshot()
            client = OpenAI()  # reads OPENAI_API_KEY from secrets/env
            prompt = f"""
You are a crypto markets analyst. Explain what the dashboard says about Solana and broader crypto.
Keep it to 6–10 bullet points. If a metric is missing, skip it.

TONE: {tone}

DATA (JSON):
{json.dumps(snapshot, separators=(',',':'))}

Guidelines:
- Start with a one-line TL;DR.
- Cover SOL vs ETH relative strength, Solana TVL trend, stablecoin liquidity trend.
- Add macro color if yield curve or VIX stands out; otherwise say 'macro neutral'.
- Mention Bullishness score level and what would move it up/down next.
- End with 2 watch-items for the coming week.
"""
            resp = client.responses.create(
                model="gpt-4o-mini",
                input=prompt,
            )
            st.markdown(resp.output_text)
        except Exception as e:
            st.error(f"GPT explain error: {e}")
            st.caption("Tip: ensure OPENAI_API_KEY is set in app Secrets. Reduce auto-refresh if you hit rate limits.")





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
