"""抓 AlphaVantage EARNINGS: 季度 SUE + 真实公布日(reportedDate/reportTime), 存盘。

AV EARNINGS 端点自带 30年历史 + reportedDate(真实公布日) + reportTime(盘前/盘后),
已用SEC 8-K交叉验证逐条一致。免费档25次/天, 故分批抓、落盘缓存, 已抓过的跳过。

用法: ./venv/bin/python fetch_earnings.py --tickers AAPL MSFT ...   (默认抓审计25只)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "cache", "earnings")


def fetch_av_earnings(ticker, key):
    url = (f"https://www.alphavantage.co/query?function=EARNINGS"
           f"&symbol={ticker}&apikey={key}")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read().decode())
    except Exception as e:
        return {"_error": str(e)}
    if "quarterlyEarnings" not in d:
        # 可能是限额提示或Note
        return {"_error": json.dumps(d)[:200]}
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", default=[
        "AAPL", "MSFT", "NVDA", "AMD", "META", "JPM", "UNH", "WMT",
        "XOM", "CAT", "COST", "LLY", "V", "HD", "CRM", "NFLX",
        "PFE", "BA", "F", "DIS", "GOOGL", "AMZN", "TSLA", "AVGO", "ORCL"])
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if not key:
        print("需 source env.sh 加载 ALPHAVANTAGE_API_KEY", file=sys.stderr); return
    os.makedirs(CACHE_DIR, exist_ok=True)

    ok, skip, fail = 0, 0, 0
    for t in args.tickers:
        path = os.path.join(CACHE_DIR, f"{t}.json")
        if os.path.exists(path):
            print(f"  {t}: 已缓存, 跳过", file=sys.stderr); skip += 1; continue
        d = fetch_av_earnings(t, key)
        if d.get("_error"):
            print(f"  {t}: 失败 {d['_error'][:100]}", file=sys.stderr); fail += 1
            if "limit" in d["_error"].lower() or "premium" in d["_error"].lower() \
               or "25 requests" in d["_error"]:
                print("  → 触及AV日配额, 停止。明天续抓(已缓存的会跳过)", file=sys.stderr)
                break
            continue
        n = len(d.get("quarterlyEarnings", []))
        with open(path, "w") as f:
            json.dump(d, f)
        print(f"  {t}: {n}季 已存", file=sys.stderr); ok += 1
        time.sleep(args.sleep)
    print(f"\n完成: 新抓{ok} 跳过{skip} 失败{fail}. 缓存目录 {CACHE_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
