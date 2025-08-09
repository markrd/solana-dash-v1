# -----------------------------
# Solana Macro Dashboard (clean restart)
# -----------------------------
# Safe-start choices:
# - Forces a local .streamlit dir to avoid '/.streamlit' permission errors.
# - ChatGPT block only runs if OPENAI_API_KEY is set AND 'openai' is installed.
# - FRED is optional (PMI & M2 show info if no key).
# - Pinned deps + Python 3.11 via runtime.txt for stable builds.

import os, time, json
from pathlib import Path
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

# --- Ensure a writable config directory (fixes '/.streamlit' PermissionError) ---
try:
    Path(".streamlit").mkdir(exist_ok=True)
    os.environ.setdefault("HOME", str(Path.cwd()))
    os.environ.setdefault("STREAMLIT_GLOBAL_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
    os.environ.setdefault("STREAMLIT_CONFIG_DIR", str(Path.cwd() / ".streamlit"))
    cfg = Path(".streamlit") / "config.toml"
    if not cfg.exists():
        cfg.write_text("browser.gatherUsageStats = false\n", encoding="utf-8")
except Exception:
    pass

import requests
import pandas as pd
import streamlit as st
import altair as alt
from datetime import datetime, date, timezone, timedelta
from streamlit_autorefresh import st_autorefresh

# ==============================
# Flags & Keys
# ==============================
FRED_API_KEY   = os.getenv("FRED_API_KEY", "08f14b68a7f881c18fe9bf5de19d85bc")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "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")
ENABLE_GPT = bool(OPENAI_API_KEY)  # we’ll also check import availability later

# ==============================
# Page & minimal "Google-y" styling
# ==============================
st.set_page_config(page_title="Solana Macro Dashboard", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
:root {
  --card-bg: #0f1116; --card-pad: 1rem; --card-radius: 16px; --shadow: 0 10px 30px rgba(0,0,0,0.18);
  --muted: #9aa3ab;
}
.block-container { padding-top: .75rem; }
.card        { background: var(--card-bg); padding: var(--card-pad); border-radius: var(--card-radius); box-shadow: var(--shadow); }
.kpi         { font-size: 1.8rem; font-weight: 700; line-height: 1.2; }
.kpi-label   { color: var(--muted); font-size: .85rem; margin-bottom: .25rem; }
.small       { color: var(--muted); font-size: .85rem; }
hr { border: none; border-top: 1px solid rgba(255,255,255,0.08); margin: .85rem 0 1.1rem; }
</style>
""", unsafe_allow_html=True)

# ==============================
# Sidebar controls
# ==============================
with st.sidebar:
    st.header("Controls")
    auto = st.checkbox("Auto-refresh", value=False, help="Enable periodic refresh")
    interval = st.slider("Interval (sec)", 15, 180, 60, 5)
    if auto:
        st_autorefresh(interval=interval*1000, key="auto_refresh_tick")
    st.caption(f"Last refreshed (UTC+4): {(datetime.now(timezone(timedelta(hours=4))).strftime('%Y-%m-%d %H:%M:%S'))}")

# ==============================
# HTTP helpers (cached + backoff)
# ==============================
@st.cache_data(ttl=600)
def http_json(url, params=None, tries=3, timeout=12):
    """GET JSON with exponential backoff; raises for non-429 errors."""
    for i in range(tries):
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code == 429 and i < tries - 1:
            time.sleep(1.5*(2**i))
            continue
        r.raise_for_status()
        return r.json()
    return None

# ==============================
# CoinGecko helpers
# ==============================
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

@st.cache_data(ttl=3600)
def cg_market_caps_sum(ids=("tether","usd-coin","dai"), days=90):
    """Sum market_caps for a few stablecoins as a liquidity proxy."""
    dfs = []
    for coin in ids:
        js = http_json(f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
                       params={"vs_currency": "usd", "days": days})
        caps = (js or {}).get("market_caps", [])
        if not caps: continue
        df = pd.DataFrame(caps, columns=["ts","cap"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df[["date","cap"]]
        df.rename(columns={"cap": f"cap_{coin}"}, inplace=True)
        dfs.append(df)
    if not dfs:
        return pd.DataFrame(columns=["date","total_cap"])
    out = dfs[0]
    for df in dfs[1:]:
        out = pd.merge_asof(out.sort_values("date"), df.sort_values("date"),
                            on="date", direction="nearest", tolerance=pd.Timedelta("1H"))
    out["total_cap"] = out.drop(columns=["date"]).sum(axis=1, min_count=1)
    return out[["date","total_cap"]].dropna()

# ==============================
# DeFiLlama helpers
# ==============================
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

# ==============================
# FRED helpers (safe)
# ==============================
@st.cache_data(ttl=3600)
def fred_series(series_id, start="2015-01-01"):
    if not FRED_API_KEY:
        return pd.DataFrame(columns=["date","value"])
    try:
        js = http_json(
            "https://api.stlouisfed.org/fred/series/observations",
            {
                "series_id": series_id,
                "api_key": FRED_API_KEY,
                "file_type": "json",
                "observation_start": start,
            },
        )
    except Exception:
        return pd.DataFrame(columns=["date","value"])
    obs = (js or {}).get("observations", [])
    if not obs:
        return pd.DataFrame(columns=["date","value"])
    df = pd.DataFrame(obs)
    df["date"]  = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna()

@st.cache_data(ttl=3600)
def macro_pmi_ytd():
    """Try ISM PMI (NAPM), then Markit PMI (PMI), else INDPRO as proxy."""
    start = f"{date.today().year-5}-01-01"
    for sid, label in [("NAPM","ISM PMI"), ("PMI","Markit PMI"), ("INDPRO","Industrial Production (proxy)")]:
        df = fred_series(sid, start=start)
        if not df.empty:
            return df, label
    return pd.DataFrame(columns=["date","value"]), "PMI"

@st.cache_data(ttl=3600)
def macro_m2_ytd():
    return fred_series("M2SL", start=f"{date.today().year-5}-01-01")

# ==============================
# Utilities
# ==============================
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

def pctfmt(x): return "—" if x is None else f"{x:+.1f}%"

def usd_big(x):
    if x is None: return "—"
    try:
        x = float(x)
        if x>=1e12: return f"${x/1e12:,.2f}T"
        if x>=1e9:  return f"${x/1e9:,.1f}B"
        if x>=1e6:  return f"${x/1e6:,.0f}M"
        return f"${x:,.0f}"
    except: return "—"

def alt_line(df, x="date", y="value", y_title="", height=240):
    return alt.Chart(df).mark_line().encode(
        x=alt.X(f"{x}:T", title=""),
        y=alt.Y(f"{y}:Q", title=y_title),
        tooltip=[alt.Tooltip(f"{x}:T"), alt.Tooltip(f"{y}:Q", format=".2f")]
    ).properties(height=height)

def _to_num(x):
    try: return None if x is None else float(x)
    except: return None

# ==============================
# Header (prices)
# ==============================
prices = cg_simple_prices("solana,ethereum,bitcoin")
sol = prices.get("solana",{}); eth = prices.get("ethereum",{}); btc = prices.get("bitcoin",{})

h1, h2, h3, h4 = st.columns([1.2, 1, 1, 1])
with h1:
    st.markdown("<div class='card'><div class='kpi'>Solana Macro Dashboard</div><div class='small'>YTD macro & crypto changes • on-chain context • score & alerts</div></div>", unsafe_allow_html=True)
with h2:
    st.markdown(f"<div class='card'><div class='kpi-label'>SOL</div><div class='kpi'>{sol.get('usd','—')}</div><div class='small'>{sol.get('usd_24h_change',0):+.1f}% / 24h</div></div>", unsafe_allow_html=True)
with h3:
    st.markdown(f"<div class='card'><div class='kpi-label'>ETH</div><div class='kpi'>{eth.get('usd','—')}</div><div class='small'>{eth.get('usd_24h_change',0):+.1f}% / 24h</div></div>", unsafe_allow_html=True)
with h4:
    st.markdown(f"<div class='card'><div class='kpi-label'>BTC</div><div class='kpi'>{btc.get('usd','—')}</div><div class='small'>{btc.get('usd_24h_change',0):+.1f}% / 24h</div></div>", unsafe_allow_html=True)

# ==============================
# ChatGPT explainer (optional)
# ==============================
st.markdown("### 🧠 Explain this dashboard")
with st.expander("Generate a quick read (uses your OpenAI key if set)"):
    tone = st.selectbox("Tone", ["Concise bullets", "Narrative summary", "Risk-focused", "Beginner-friendly"], key="tone_top")
    if st.button("Generate commentary", key="gen_top"):
        try:
            # Snapshot
            tvl_df = llama_solana_tvl()
            tvl_latest = None if tvl_df.empty else float(tvl_df.iloc[-1]["tvl"])
            tvl_30d = None
            if not tvl_df.empty and len(tvl_df) > 30:
                base = float(tvl_df.iloc[-30]["tvl"]); last = float(tvl_df.iloc[-1]["tvl"])
                tvl_30d = None if base == 0 else (last/base - 1)*100.0

            # Relative strength
            rel_30d = None; rs_latest = None
            try:
                sol_df = cg_market_chart("solana", days=120)
                eth_df = cg_market_chart("ethereum", days=120)
                merged = pd.merge_asof(sol_df.sort_values("date"), eth_df.sort_values("date"),
                                       on="date", direction="nearest", tolerance=pd.Timedelta("1H"),
                                       suffixes=("_sol","_eth")).dropna()
                merged["sol_eth_ratio"] = merged["price_sol"] / merged["price_eth"]
                if not merged.empty:
                    rs_latest = float(merged.iloc[-1]["sol_eth_ratio"])
                    ma30 = float(merged["sol_eth_ratio"].tail(30).mean())
                    rel_30d = None if ma30 == 0 else (rs_latest/ma30 - 1)*100.0
            except Exception:
                pass

            # Macro quick
            yc_spread = None; vix_v = None
            try:
                dgs10 = fred_series("DGS10", start="2015-01-01")
                dgs2  = fred_series("DGS2",  start="2015-01-01")
                vix   = fred_series("VIXCLS",start="2015-01-01")
                def _latest(df): 
                    try: return None if df.empty else float(df.dropna().iloc[-1]["value"])
                    except: return None
                t10, t2, vix_v = _latest(dgs10), _latest(dgs2), _latest(vix)
                yc_spread = (t10 - t2) if (t10 is not None and t2 is not None) else None
            except Exception:
                pass

            snapshot = {
                "timestamp_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "prices": {"sol_usd": _to_num(sol.get("usd")), "eth_usd": _to_num(eth.get("usd")), "btc_usd": _to_num(btc.get("usd"))},
                "tvl": {"latest": _to_num(tvl_latest), "chg_30d_pct": _to_num(tvl_30d)},
                "relative_strength": {"sol_eth_ratio_latest": _to_num(rs_latest), "vs_30d_avg_pct": _to_num(rel_30d)},
                "macro": {"yield_curve_10y_minus_2y": _to_num(yc_spread), "vix": _to_num(vix_v)},
                "bullishness_score": _to_num(st.session_state.get("last_bull_score")),
            }

            if ENABLE_GPT:
                try:
                    from openai import OpenAI  # only import if they actually clicked + key provided
                    client = OpenAI()
                    prompt = f"""
You are a crypto markets analyst. Explain what the dashboard says about Solana and broader crypto.
Keep it to 6–10 bullet points. If a metric is missing, skip it.

TONE: {tone}

DATA (JSON):
{json.dumps(snapshot, separators=(',',':'))}

Guidelines:
- Start with a one-line TL;DR.
- Cover SOL vs ETH relative strength, Solana TVL trend, stablecoin liquidity trend (if available).
- Add macro color if yield curve or VIX stands out; otherwise say 'macro neutral'.
- Mention Bullishness score level and what would move it up/down next.
- End with 2 watch-items for the coming week.
"""
                    resp = client.responses.create(model="gpt-4o-mini", input=prompt, max_output_tokens=300, temperature=0.3)
                    st.markdown(resp.output_text)
                except Exception as e:
                    st.error(f"GPT call error: {e}")
                    st.caption("Tip: add OPENAI_API_KEY in Secrets and (later) add openai==1.35.10 to requirements.")
            else:
                st.info("No OPENAI_API_KEY set. Showing a lightweight local summary.")
                lines = []
                lines.append(f"**TL;DR:** Score {snapshot.get('bullishness_score') or '—'}/100; "
                             f"macro {'neutral' if not snapshot['macro'].get('vix') or snapshot['macro']['vix']<25 else 'elevated risk'}.")
                if snapshot["relative_strength"]["vs_30d_avg_pct"] is not None:
                    lines.append(f"- SOL/ETH vs 30d avg: {snapshot['relative_strength']['vs_30d_avg_pct']:+.1f}%.")
                if snapshot["tvl"]["chg_30d_pct"] is not None:
                    lines.append(f"- Solana TVL 30d: {snapshot['tvl']['chg_30d_pct']:+.1f}%.")
                if snapshot["macro"]["vix"] is not None:
                    lines.append(f"- VIX: {snapshot['macro']['vix']:.1f}.")
                if snapshot["macro"]["yield_curve_10y_minus_2y"] is not None:
                    yc = snapshot["macro"]["yield_curve_10y_minus_2y"]
                    lines.append(f"- Yield curve (10y-2y): {yc:.2f}% ({'inverted' if yc<0 else 'normal'}).")
                lines.append("- Watch next: sustain TVL uptick; SOL/ETH above 30d average.")
                st.markdown("\n".join(lines))
        except Exception as e:
            st.error(f"Explain error: {e}")

st.markdown("<hr/>", unsafe_allow_html=True)

# ==============================
# YTD Charts (PMI, M2, SOL, ETH, BTC)
# ==============================
st.subheader("YTD (in-year) changes")
left, right = st.columns(2)

with left:
    pmi_df, pmi_label = macro_pmi_ytd()
    pmi_ytd = ytd_only(pmi_df)
    st.markdown(f"**{pmi_label} — YTD change: {pctfmt(ytd_change(pmi_ytd))}**")
    if not pmi_ytd.empty:
        st.altair_chart(alt_line(pmi_ytd, "date", "value", pmi_label, 260), use_container_width=True)
    else:
        st.info("No PMI/INDPRO data (add a FRED key to enable).")

    sol_hist = cg_market_chart("solana", days=370)
    sol_ytd = ytd_only(sol_hist, "date")
    if not sol_ytd.empty:
        base = float(sol_ytd.iloc[0]["price"])
        sol_ytd["change_pct"] = (sol_ytd["price"]/base - 1)*100
        st.markdown(f"**SOL — YTD change: {pctfmt(float(sol_ytd['change_pct'].iloc[-1]))}**")
        st.altair_chart(alt_line(sol_ytd, "date", "change_pct", "SOL % from Jan 1", 260), use_container_width=True)
    else:
        st.info("No SOL history (rate limit?)")

with right:
    m2_df = macro_m2_ytd()
    m2_ytd = ytd_only(m2_df)
    st.markdown(f"**M2 Money Stock — YTD change: {pctfmt(ytd_change(m2_ytd))}**")
    if not m2_ytd.empty:
        st.altair_chart(alt_line(m2_ytd, "date", "value", "M2 (NSA)", 260), use_container_width=True)
    else:
        st.info("No M2 data (add a FRED key).")

    for coin_id, label in [("ethereum","ETH"), ("bitcoin","BTC")]:
        hist = cg_market_chart(coin_id, days=370)
        ytd = ytd_only(hist, "date")
        if not ytd.empty:
            base = float(ytd.iloc[0]["price"])
            ytd["change_pct"] = (ytd["price"]/base - 1)*100
            st.markdown(f"**{label} — YTD change: {pctfmt(float(ytd['change_pct'].iloc[-1]))}**")
            st.altair_chart(alt_line(ytd, "date", "change_pct", f"{label} % from Jan 1", 180), use_container_width=True)
        else:
            st.info(f"No {label} history (rate limit?)")

st.markdown("<hr/>", unsafe_allow_html=True)

# ==============================
# On-chain Liquidity & TVL
# ==============================
st.subheader("On-chain Liquidity & TVL")
colA, colB = st.columns([1,2])

with colA:
    total, _raw = defillama_stablecoins_total()
    st.markdown(
        f"<div class='card'><div class='kpi-label'>Stablecoin Market Cap (approx)</div>"
        f"<div class='kpi'>{usd_big(total)}</div><div class='small'>Source: DeFiLlama (global)</div></div>",
        unsafe_allow_html=True
    )

with colB:
    tvl_df = llama_solana_tvl()
    if not tvl_df.empty:
        st.altair_chart(alt_line(tvl_df, "date", "tvl", "Solana TVL (USD)", 260), use_container_width=True)
        st.caption(f"Latest TVL: ${float(tvl_df.iloc[-1]['tvl']):,.0f}")
    else:
        st.info("No TVL data returned (try again)")

st.markdown("<hr/>", unsafe_allow_html=True)

# ==============================
# SOL / ETH Relative Strength
# ==============================
st.subheader("SOL vs ETH — Relative Strength")
try:
    sol_df = cg_market_chart("solana", days=365)
    eth_df = cg_market_chart("ethereum", days=365)
    if sol_df.empty or eth_df.empty:
        st.warning("Couldn’t load history from CoinGecko (rate limit?).")
    else:
        merged = pd.merge_asof(
            sol_df.sort_values("date"),
            eth_df.sort_values("date"),
            on="date", direction="nearest", tolerance=pd.Timedelta("1H"),
            suffixes=("_sol", "_eth")
        ).dropna()
        merged["sol_eth_ratio"] = merged["price_sol"] / merged["price_eth"]
        ch = alt.Chart(merged).mark_line().encode(
            x=alt.X("date:T", title=""),
            y=alt.Y("sol_eth_ratio:Q", title="SOL/ETH"),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("sol_eth_ratio:Q", format=".4f")]
        ).properties(height=260)
        st.altair_chart(ch, use_container_width=True)
        latest = merged.iloc[-1]["sol_eth_ratio"]
        ma30 = merged["sol_eth_ratio"].tail(30).mean()
        st.caption(f"Latest ratio: {latest:.4f} | 30-day avg: {ma30:.4f}")
except Exception as e:
    st.error(f"SOL/ETH ratio error: {e}")

st.markdown("<hr/>", unsafe_allow_html=True)

# ==============================
# Global Context
# ==============================
st.subheader("Global Crypto Context")
@st.cache_data(ttl=600)
def cg_global():
    return http_json("https://api.coingecko.com/api/v3/global") or {}

g = cg_global().get("data", {})
total_mc = (g.get("total_market_cap", {}) or {}).get("usd")
btc_pct  = (g.get("market_cap_percentage", {}) or {}).get("btc")
btc_cap  = total_mc * (btc_pct/100.0) if total_mc and btc_pct is not None else None
alt_cap  = (total_mc - btc_cap) if (total_mc and btc_cap) else None

d1,d2,d3 = st.columns(3)
with d1: st.markdown(f"<div class='card'><div class='kpi-label'>BTC Dominance</div><div class='kpi'>{'—' if btc_pct is None else f'{btc_pct:.1f}%'}</div></div>", unsafe_allow_html=True)
with d2: st.markdown(f"<div class='card'><div class='kpi-label'>TOTAL Market Cap</div><div class='kpi'>{usd_big(total_mc)}</div></div>", unsafe_allow_html=True)
with d3: st.markdown(f"<div class='card'><div class='kpi-label'>Altcoin Market Cap</div><div class='kpi'>{usd_big(alt_cap)}</div></div>", unsafe_allow_html=True)

st.markdown("<hr/>", unsafe_allow_html=True)

# ==============================
# 30-Day Signals + Bullishness + Alerts
# ==============================
st.subheader("30-Day Trend Signals")

def pct_change_over_30d(df, col):
    if df is None or df.empty or col not in df: return None
    df = df[["date", col]].dropna()
    if df.empty: return None
    latest = df["date"].max()
    base_df = df[df["date"] >= latest - pd.Timedelta(days=30)]
    base_val = float(base_df.iloc[0][col]) if not base_df.empty else float(df.iloc[0][col])
    last_val = float(df.iloc[-1][col])
    if base_val == 0: return None
    return (last_val/base_val - 1) * 100.0

# TVL 30d
try:
    tvl_30d = pct_change_over_30d(llama_solana_tvl().rename(columns={"tvl":"value"}), "value")
except Exception:
    tvl_30d = None

# Stablecoins 30d proxy (USDT+USDC+DAI summed caps)
try:
    sc = cg_market_caps_sum(ids=("tether","usd-coin","dai"), days=90)
    stable_30d = pct_change_over_30d(sc.rename(columns={"total_cap":"value"}), "value")
except Exception:
    stable_30d = None

# SOL/ETH vs 30d avg
rel_30d = None
try:
    sol_df = cg_market_chart("solana", days=60)
    eth_df = cg_market_chart("ethereum", days=60)
    merged = pd.merge_asof(sol_df.sort_values("date"), eth_df.sort_values("date"),
                           on="date", direction="nearest", tolerance=pd.Timedelta("1H"),
                           suffixes=("_sol","_eth")).dropna()
    merged["sol_eth_ratio"] = merged["price_sol"] / merged["price_eth"]
    latest_ratio = float(merged.iloc[-1]["sol_eth_ratio"])
    ma30 = float(merged["sol_eth_ratio"].tail(30).mean())
    rel_30d = None if ma30 == 0 else (latest_ratio/ma30 - 1)*100.0
except Exception:
    pass

c1, c2, c3 = st.columns(3)
with c1: st.markdown(f"<div class='card'><div class='kpi-label'>TVL 30d</div><div class='kpi'>{'—' if tvl_30d is None else f'{tvl_30d:.1f}%'}</div></div>", unsafe_allow_html=True)
with c2: st.markdown(f"<div class='card'><div class='kpi-label'>Stablecoins 30d</div><div class='kpi'>{'—' if stable_30d is None else f'{stable_30d:.1f}%'}</div><div class='small'>USDT+USDC+DAI caps</div></div>", unsafe_allow_html=True)
with c3: st.markdown(f"<div class='card'><div class='kpi-label'>SOL/ETH vs 30d avg</div><div class='kpi'>{'—' if rel_30d is None else f'{rel_30d:.1f}%'}</div></div>", unsafe_allow_html=True)

# Bullishness score
st.subheader("Bullishness Score")
def pct_to_score(pct, pos=20.0, neg=-20.0):
    if pct is None: return None
    denom = pos if pct >= 0 else abs(neg)
    score = 50 + (pct/denom)*50
    return max(0, min(100, score))

score_tvl     = pct_to_score(tvl_30d)
score_stables = pct_to_score(stable_30d)
score_rel     = pct_to_score(rel_30d, 15.0, -15.0)
weights = {"tvl": 0.40, "stables": 0.35, "rel": 0.25}

num, den = 0.0, 0.0
for s, w in [(score_tvl, weights["tvl"]), (score_stables, weights["stables"]), (score_rel, weights["rel"])]:
    if s is not None:
        num += s*w; den += w
score = (num/den) if den>0 else None
prev = st.session_state.get("last_bull_score")

if score is None:
    st.info("Not enough data yet to compute a score.")
else:
    delta = None if prev is None else score - prev
    st.session_state["last_bull_score"] = score
    light = "🟢" if score >= 70 else ("🟠" if score >= 40 else "🔴")
    x1, x2 = st.columns([1,1])
    with x1: st.markdown(f"<div class='card'><div class='kpi-label'>Bullishness (0–100)</div><div class='kpi'>{score:.0f}</div><div class='small'>{'' if delta is None else f'Delta {delta:+.0f}'}</div></div>", unsafe_allow_html=True)
    with x2: st.markdown(f"<div class='card'><div class='kpi-label'>Signal</div><div class='kpi'>{light}</div></div>", unsafe_allow_html=True)

# Alerts
st.subheader("Alerts")
alerts = []
def add_alert(ok, msg):
    if ok: alerts.append(msg)

add_alert(tvl_30d is not None and tvl_30d <= -10, f"Solana TVL 30d is {tvl_30d:.1f}% (≤ -10%).")
add_alert(stable_30d is not None and stable_30d <= -10, f"Stablecoin cap proxy 30d is {stable_30d:.1f}% (≤ -10%).")
add_alert(rel_30d is not None and rel_30d <= -5, f"SOL/ETH vs 30d avg is {rel_30d:.1f}% (≤ -5%).")

if alerts:
    st.warning("⚠️ Risk flags active:\n\n- " + "\n- ".join(alerts))
else:
    st.success("✅ No major risk flags right now.")

st.caption("Tip: if you see 429 errors on CoinGecko, increase the auto-refresh interval.")



