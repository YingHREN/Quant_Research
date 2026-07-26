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
from concurrent.futures import ThreadPoolExecutor
import glob
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Optional

import pandas as pd

from data.daily_history import (
    completed_ingestions,
    coverage_report,
    history_start,
    persist_history,
)

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


@dataclass(frozen=True)
class BackfillSummary:
    requested: int
    skipped: int
    succeeded: int
    failed: int
    warnings: int
    below_eight_year_floor: int
    rate_limited: bool


def backfill(
    years: int = 10,
    tickers: Optional[Iterable[str]] = None,
    *,
    workers: int = 4,
    connection=None,
    fetcher=None,
    asof: Optional[date] = None,
):
    """Fetch and non-destructively persist an explicit adjusted-history window."""
    sys.path.insert(0, BASE)
    if not isinstance(years, int) or years <= 0:
        raise ValueError("years must be a positive integer")
    if not isinstance(workers, int) or not 1 <= workers <= 16:
        raise ValueError("workers must be an integer between 1 and 16")
    if fetcher is None:
        from data.fetch import fetch as fetcher
    from data.fetch import PROVIDER

    owns_connection = connection is None
    con = connection if connection is not None else sqlite3.connect(DB)
    symbols = update_tickers(local_tickers()) if tickers is None else sorted(set(tickers))
    observation_day = asof or date.today()
    requested_start = history_start(observation_day, years)
    completed = completed_ingestions(
        con,
        provider=PROVIDER,
        requested_start=requested_start,
    )
    pending_symbols = [ticker for ticker in symbols if ticker not in completed]
    skipped = len(symbols) - len(pending_symbols)
    succeeded = failed = warnings = below_floor = 0
    rate_limited = False
    period = f"{years}y"

    def fetch_one(ticker):
        try:
            return ticker, fetcher(ticker, period=period, use_cache=False)
        except Exception as error:
            return ticker, error

    if workers == 1:
        fetched = map(fetch_one, pending_symbols)
        executor = None
    else:
        executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="daily-history-fetch",
        )
        fetched = executor.map(fetch_one, pending_symbols)
    try:
        for ticker, outcome in fetched:
            if isinstance(outcome, BaseException):
                text = str(outcome)
                if "429" in text:
                    print(
                        f"  {ticker}: 429限流，停止回填（已完成{succeeded}只）",
                        file=sys.stderr,
                    )
                    rate_limited = True
                    break
                failed += 1
                print(f"  {ticker}: {text or '供应商请求失败'}", file=sys.stderr)
                continue
            sd = outcome
            if not sd.ok:
                if "429" in (sd.error or ""):
                    print(
                        f"  {ticker}: 429限流，停止回填（已完成{succeeded}只）",
                        file=sys.stderr,
                    )
                    rate_limited = True
                    break
                failed += 1
                print(f"  {ticker}: {sd.error or '供应商返回失败'}", file=sys.stderr)
                continue
            try:
                coverage = persist_history(
                    con,
                    ticker,
                    sd.history,
                    provider=PROVIDER,
                    adjustment="split_dividend_adjusted",
                    requested_start=requested_start,
                    fetched_at=datetime.now(timezone.utc),
                )
            except Exception as error:
                failed += 1
                print(f"  {ticker}: 数据校验失败: {error}", file=sys.stderr)
                continue
            succeeded += 1
            warnings += coverage.quality_status == "warning"
            below_floor += not coverage.meets_eight_year_floor
            if workers == 1:
                time.sleep(0.3)
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
    if owns_connection:
        con.close()
    summary = BackfillSummary(
        requested=len(symbols),
        skipped=skipped,
        succeeded=succeeded,
        failed=failed,
        warnings=warnings,
        below_eight_year_floor=below_floor,
        rate_limited=rate_limited,
    )
    print(
        f"{years}年窗口：请求{summary.requested}只，跳过已完成{summary.skipped}只，"
        f"本次成功{summary.succeeded}只，"
        f"失败{summary.failed}只，质量警告{summary.warnings}只，"
        f"不足8年{summary.below_eight_year_floor}只"
    )
    return summary


def update():
    """Fetch a one-year overlap and upsert corrections without truncating history."""
    return backfill(years=1, workers=1)


def print_coverage():
    con = sqlite3.connect(DB)
    rows = coverage_report(con)
    price_stats = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM prices"
    ).fetchone()
    con.close()
    meets = sum(row.meets_eight_year_floor for row in rows)
    warnings = sum(row.quality_status == "warning" for row in rows)
    print(
        f"价格库：{price_stats[0]}行，{price_stats[1]}只，"
        f"{price_stats[2]} ~ {price_stats[3]}"
    )
    print(
        f"已审计：{len(rows)}只，达到8年{meets}只，不足8年{len(rows) - meets}只，"
        f"质量警告{warnings}只"
    )
    for row in rows:
        if not row.meets_eight_year_floor or row.quality_status != "ok":
            print(
                f"  {row.ticker}: {row.first_date} ~ {row.last_date}, "
                f"{row.coverage_years:.2f}年, {row.quality_status}, "
                f"异常跳变{row.suspicious_returns}, 长缺口{row.long_gaps}"
            )
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="增量联网更新(否则从缓存重建)")
    ap.add_argument(
        "--backfill-years",
        type=int,
        help="联网回填指定年数的复权日线；DATA-001 默认使用10",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=4,
        help="长期回填并发下载数（1～16，默认4；写库仍为单线程）",
    )
    ap.add_argument("--coverage", action="store_true", help="打印长期数据覆盖和质量审计")
    args = ap.parse_args()
    selected = sum(
        (bool(args.update), args.backfill_years is not None, bool(args.coverage))
    )
    if selected > 1:
        ap.error("--update、--backfill-years 和 --coverage 只能选择一个")
    if args.coverage:
        print_coverage()
    elif args.backfill_years is not None:
        backfill(args.backfill_years, workers=args.workers)
    elif args.update:
        update()
    else:
        n, rows = build()
        print(f"从 {n} 个缓存pkl建库完成")


if __name__ == "__main__":
    main()
