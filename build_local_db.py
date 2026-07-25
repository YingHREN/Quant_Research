"""本地价格数据库 —— 一劳永逸解决Tiingo 429限流。

问题: 回测/消融每次重拉网络数据(175只×2年), 密集请求撞429。
方案(数据层与回测层分离): 把已缓存的StockData pkl合并成本地SQLite长表,
回测【只读本地, 永不联网】。每日只需增量抓当天1根K线追加(见update_local)。

用法:
  ./venv/bin/python build_local_db.py            # 从现有缓存建/重建 prices.db
  ./venv/bin/python build_local_db.py --update    # 增量: 联网抓各票最新缺失的K线追加
  from build_local_db import load_local           # 回测读本地: load_local("NVDA") -> DataFrame
"""
from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "data", "cache")
DB = os.path.join(BASE, "data", "prices.db")


def _latest_pkl_per_ticker():
    """每只票取日期最新的 tiingo 缓存 pkl。返回 {ticker: path}。"""
    latest = {}
    for f in glob.glob(os.path.join(CACHE, "*_tiingo_*.pkl")):
        b = os.path.basename(f)
        t = b.split("_")[0]
        d = b.split("_")[-1].replace(".pkl", "")
        if t not in latest or d > latest[t][1]:
            latest[t] = (f, d)
    return {t: v[0] for t, v in latest.items()}


def build():
    """从缓存 pkl 合并成 SQLite 长表 prices(ticker, date, open, high, low, close, volume)。"""
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS prices(
        ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
        PRIMARY KEY(ticker, date))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ticker ON prices(ticker)")
    pkls = _latest_pkl_per_ticker()
    n_rows = 0
    for t, path in pkls.items():
        try:
            sd = pd.read_pickle(path)
            h = sd.history if hasattr(sd, "history") else sd
            if h is None or len(h) == 0:
                continue
        except Exception as e:
            print(f"  跳过 {t}: {e}", file=sys.stderr); continue
        rows = [(t, str(idx.date()), float(r["Open"]), float(r["High"]),
                 float(r["Low"]), float(r["Close"]), float(r["Volume"]))
                for idx, r in h.iterrows()]
        con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
        n_rows += len(rows)
    con.commit()
    stat = con.execute("SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM prices").fetchone()
    con.close()
    print(f"本地库 {DB}: {stat[0]}行  {stat[1]}只票  {stat[2]} ~ {stat[3]}")
    return len(pkls), n_rows


def load_local(ticker):
    """回测用: 从本地库读一只票的OHLCV, 返回DatetimeIndex DataFrame(与StockData.history同格式)。
    库不存在或无该票 → 返回空DataFrame。"""
    if not os.path.exists(DB):
        return pd.DataFrame()
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM prices "
        "WHERE ticker=? ORDER BY date", con, params=(ticker,))
    con.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df.columns = ["Open", "High", "Low", "Close", "Volume"]
    df.index.name = "Date"
    return df


def local_tickers():
    if not os.path.exists(DB):
        return []
    con = sqlite3.connect(DB)
    ts = [r[0] for r in con.execute("SELECT DISTINCT ticker FROM prices").fetchall()]
    con.close()
    return ts


def update_tickers(existing):
    """Include model reference series even before they exist in the database."""
    from web.market_groups import REFERENCE_TICKERS

    return sorted(set(existing).union(REFERENCE_TICKERS))


def update():
    """增量: 对库中每只票, 联网抓最新缺失的K线追加(只补增量, 不重拉全history)。
    受限流影响时用重试; 撞墙就停(已抓的保留)。"""
    sys.path.insert(0, BASE)
    from data.fetch import fetch
    import time
    con = sqlite3.connect(DB)
    tickers = update_tickers(local_tickers())
    updated = 0
    for t in tickers:
        cur_max = con.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (t,)).fetchone()[0]
        sd = fetch(t)
        if not sd.ok:
            if "429" in (sd.error or ""):
                print(f"  {t}: 429限流, 停止增量(已更新{updated}只)", file=sys.stderr); break
            continue
        h = sd.history
        new = h[h.index > pd.Timestamp(cur_max)] if cur_max else h
        if len(new):
            rows = [(t, str(idx.date()), float(r["Open"]), float(r["High"]),
                     float(r["Low"]), float(r["Close"]), float(r["Volume"]))
                    for idx, r in new.iterrows()]
            con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
            con.commit(); updated += 1
        time.sleep(0.3)
    con.close()
    print(f"增量更新 {updated} 只")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="增量联网更新(否则从缓存重建)")
    args = ap.parse_args()
    if args.update:
        update()
    else:
        n, rows = build()
        print(f"从 {n} 个缓存pkl建库完成")


if __name__ == "__main__":
    main()
