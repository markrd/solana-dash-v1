import os, time
import requests
import pandas as pd
import streamlit as st
import altair as alt
from datetime import datetime, timezone, timedelta

# -----------------------------------------------------------------------------
# Page + Modern UI (no extra deps)
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Solana Dashboard — v2.3 (modern)", layout="wide")

MODERN_STYLE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0b0f19; --bg2:#0f1322; --card:#0f1322; --text:#eaf0f6; --muted:#9aa3ab; --border:rgba(255,255,255,.065);
  --accent:#22d3ee; --up:#2dd4bf; --down:#fb7185;
}
html,body,[class^="css"]{font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,"Helvetica Neue",Arial,"Noto Sans","Apple Color Emoji","Segoe UI Emoji","Segoe UI Symbol";}
.block-container{padding-top: 1rem; max-width: 1200px;}
.card{background:var(--card); border:1px solid var(--border); border-radius:16px; padding:16px 18px;}
.kpi{display:flex; flex-direction:column; gap:6px;}
.kpi .label{color:var(--muted); font-size:.85rem;}
.kpi .value{font-weight:700; font-size:1.75rem; line-height:1.1}
.kpi .sub{color:var(--muted); font-size:.85rem;}
.kpi .delta.up{color:var(--up); font-weight:600}
.kpi .delta.down{color:var(--down); font-weight:600}
.sec{display:flex; justify-content:space-between; align-items:center; margin:6px 0 10px}
.sec h3{margin:0; font-size:1.1rem}
.sec .hint{color:var(--muted); font-size:.9rem}
.hero{display:flex; align-items:flex-end; justify-content:space-between; gap:10px; margin-bottom:10px}
.hero h1{margin:0; font-size:2.1rem}
.badge{display:inline-block; margin-left:8px; background:rgba(34,211,238,.12); border:1px solid var(--accent);
  color:var(--accent); padding:2px 8px; border-radius:999px; font-size:.85rem}
.vega-embed, .element-container:has(canvas){background:var(--card); border:1px solid var(--border); border-radius:16px; padding:8px}
</style>
"""
st.markdown(MODERN_STYLE, unsafe_allow_html=True)

def ui_hero(title:str, subtitle:str="", badge:str=""):
    b = f'<span class="badge">{badge}</span>' if badge else ""
    return f'''<div class="hero"><div>
        <h1>{title} {b}</h1>
        <div class="hint">{subtitle}</div>
    </div></div>'''

def ui_kpi(label:str, value:str, sub:str="", delta:float|None=None):
    d_html=""
    if isinstance(delta, (int,float)):
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

# Altair theme (clean dark)
alt.themes.register('clean_dark', lambda: {
    "config": {
        "background": "transparent",
        "view": {"stroke": "transparent"},
        "axis": {"labelColor": "#cdd5df", "titleColor": "#cdd5df", "gridColor": "#1f2a3a", "grid": True},
        "legend": {"labelColor": "#cdd5df", "titleColor": "#cdd5df"},
        "range": {"category": ["#22d3ee", "#93c5fd", "#60a5fa", "#34d399", "#f59e0b", "#fb7185"]}
    }
})
alt.themes.enable('clean_dark')

# -----------------------------------------------------------------------------
# HTTP helpers (with 429 backoff)
# -----------------------------------------------------------------------------
def http_json(url, params=None, tries=3, timeout=10):
    for i in range(tries):
        r = requests.get(url, params=params, timeout=timeout)
        # Backoff on 429
        if r.status_code == 429 and i < tries-1:
            time.sleep(1.5*(2**i))
            continue
        r.raise_for_status()
        return r.json()
    return None

# -----------------------------------------------------------------------------
# Prices (CoinGecko → CoinCap fallback)
# -----------------------------------------------------------------------------
def get_prices():
    """
    Returns dict:
    {
      'solana': {'usd': float, 'usd_24h_change': float},
      'ethereum': {...},
      'bitcoin': {...}
    }
    """
    ids = "solana,ethereum,bitcoin"
    # Try CoinGecko
    try:
        js = http_json(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"},
            tries=3, timeout=8
        ) or {}
        if js:
            return js
    except Exception:
        pass

    # Fallback: CoinCap
    try:
        js = http_json("https://api.coincap.io/v2/assets", params={"ids": ids.replace(",","%2C")}, tries=3, timeout=8) or {}
        out = {}
        for a in (js.get("data") or []):
            key = a["id"]  # 'bitcoin','ethereum','solana'
            out[key] = {
                "usd": float(a.get("priceUsd")) if a.get("priceUsd") else None,
                "usd_24h_change": float(a.get("changePercent24Hr")) if a







