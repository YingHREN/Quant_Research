"""Web 面板：Flask 应用，输入一组股票代码，展示评分表和明细。

用法:
    python web/app.py
    浏览器打开 http://127.0.0.1:5000
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request

from data.fetch import fetch, fetch_benchmark
from factors.compute import compute_all
from scoring.engine import evaluate
from run import market_uptrend, DEMO_TICKERS

app = Flask(__name__)


def analyze(tickers):
    bench = fetch_benchmark()
    mkt_ok = market_uptrend(bench)
    rows = []
    for t in tickers:
        sd = fetch(t)
        if not sd.ok:
            rows.append({"ticker": t.upper(), "error": sd.error})
            continue
        f = compute_all(sd, bench)
        r = evaluate(f, mkt_ok)
        rows.append({
            "ticker": r.ticker,
            "total": r.total,
            "grade": r.grade,
            "passed": r.passed_filter,
            "buyable": r.trigger["buyable_now"],
            "rs": f["rs"],
            "eps_yoy": None if f["fundamentals"].get("eps_yoy") is None else round(f["fundamentals"]["eps_yoy"], 1),
            "rev_yoy": None if f["fundamentals"].get("rev_yoy") is None else round(f["fundamentals"]["rev_yoy"], 1),
            "pct_from_high": None if f["hl52"]["pct_from_high"] is None else round(f["hl52"]["pct_from_high"], 1),
            "breakdown": r.breakdown,
            "vcp": f["vcp"],
            "pivot": f["pivot"],
            "trigger": r.trigger,
            "gaps": r.data_gaps,
            "error": None,
        })
    rows.sort(key=lambda x: x.get("total", -1), reverse=True)
    return rows, mkt_ok


@app.route("/", methods=["GET", "POST"])
def index():
    rows, mkt_ok, query = None, None, ""
    if request.method == "POST":
        query = request.form.get("tickers", "").strip()
        tickers = [t.strip() for t in query.replace(",", " ").split() if t.strip()]
        if not tickers:
            tickers = DEMO_TICKERS
            query = " ".join(DEMO_TICKERS)
        rows, mkt_ok = analyze(tickers)
    return render_template("index.html", rows=rows, mkt_ok=mkt_ok, query=query)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
