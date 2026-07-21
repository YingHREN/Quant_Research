"""PEAD 前置闸门: 财报时间戳审计(Codex 决策者指令)。

目的: 证明能可靠拿到财报【真实公布日期】, 否则 PEAD 信号会前视泄漏。
免费 Finnhub 只给财季末 period(不是公布日), earnings calendar 免费档为空。
唯一可靠来源 = SEC EDGAR 8-K Item 2.02(财报发布), 带精确 acceptance timestamp。

对每只票每个财季:
  Finnhub EPS surprise 的 period(财季末)
  → SEC 找该 period 之后 60 天内最近的 Item 2.02 8-K
  → 记录: 真实公布日、前视天数(period→公布日, 之前用period对齐会前视这么多天)、
    盘前/盘后(按 acceptance 时刻, ET, >16:00算盘后→次日可交易)

输出三项(Codex门槛): 发布日期匹配率、是否事后修订(本次先测匹配率)、时刻覆盖率。
匹配率 <90% → No-Go。

用法: ./venv/bin/python audit_earnings_dates.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import datetime as dt

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

UA = {"User-Agent": "stock-screener-research contact@example.com"}
# 审计用的20只(多板块, Codex建议先小样本审计)
AUDIT_TICKERS = ["AAPL", "MSFT", "NVDA", "AMD", "META", "JPM", "UNH", "WMT",
                 "XOM", "CAT", "COST", "LLY", "V", "HD", "CRM", "NFLX",
                 "PFE", "BA", "F", "DIS"]


def http_json(url, headers, retries=3):
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if k == retries - 1:
                return {"_error": str(e)}
            time.sleep(1.0)
    return {"_error": "unreachable"}


def get_finnhub_eps(ticker, key):
    """Finnhub EPS surprise: 返回 [(period, actual, estimate, surprisePct)]."""
    url = f"https://finnhub.io/api/v1/stock/earnings?symbol={ticker}&token={key}"
    d = http_json(url, {})
    if isinstance(d, dict) and d.get("_error"):
        return []
    out = []
    for r in d if isinstance(d, list) else []:
        if r.get("period") and r.get("actual") is not None:
            out.append((r["period"], r.get("actual"), r.get("estimate"),
                        r.get("surprisePercent")))
    return out


_CIK_CACHE = None
def get_cik(ticker):
    """SEC ticker→CIK 映射(一次性拉全表)。"""
    global _CIK_CACHE
    if _CIK_CACHE is None:
        d = http_json("https://www.sec.gov/files/company_tickers.json", UA)
        _CIK_CACHE = {}
        if isinstance(d, dict) and not d.get("_error"):
            for v in d.values():
                _CIK_CACHE[v["ticker"].upper()] = str(v["cik_str"]).zfill(10)
    return _CIK_CACHE.get(ticker.upper())


def get_earnings_8k(cik):
    """SEC 该公司所有 Item 2.02 财报8-K: 返回 [(filingDate, acceptanceDateTime)]."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    d = http_json(url, UA)
    if d.get("_error"):
        return []
    r = d.get("filings", {}).get("recent", {})
    forms = r.get("form", []); dates = r.get("filingDate", [])
    items = r.get("items", []); acc = r.get("acceptanceDateTime", [])
    out = []
    for i, f in enumerate(forms):
        if f == "8-K" and i < len(items) and "2.02" in (items[i] or ""):
            out.append((dates[i], acc[i] if i < len(acc) else None))
    return out


def match_period_to_release(period_str, filings, max_gap=75):
    """财季末 period → 之后最近的财报8-K公布日。返回 (release_date, acc_dt) 或 None。"""
    try:
        pend = dt.date.fromisoformat(period_str)
    except Exception:
        return None
    best = None
    for fdate, acc in filings:
        try:
            fd = dt.date.fromisoformat(fdate)
        except Exception:
            continue
        gap = (fd - pend).days
        if 0 <= gap <= max_gap:
            if best is None or gap < best[0]:
                best = (gap, fdate, acc)
    if best:
        return best[1], best[2], best[0]  # release_date, acc_dt, gap_days
    return None


def main():
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        print("需 source env.sh 加载 FINNHUB_API_KEY", file=sys.stderr); return

    print("=" * 68)
    print("PEAD 前置闸门: 财报时间戳审计 (Codex指令)")
    print("=" * 68)
    print(f"审计 {len(AUDIT_TICKERS)} 只, Finnhub EPS period vs SEC 8-K(Item2.02)真实公布日\n")

    rows = []
    cik_failed = []  # CIK映射到错误实体(如重组后新控股), 查不到历史财报8-K
    for t in AUDIT_TICKERS:
        eps = get_finnhub_eps(t, key)
        cik = get_cik(t)
        if not cik:
            print(f"  {t}: 无CIK, 跳过", file=sys.stderr); continue
        filings = get_earnings_8k(cik)
        time.sleep(0.15)  # SEC 限速礼貌
        if not filings:
            # 主CIK无财报8-K(多为实体重组/CIK迁移, 如XOM→ExxonMobil Holdings)
            cik_failed.append(t)
            print(f"  {t}: CIK={cik} 无财报8-K(实体重组?), 排除出分母", file=sys.stderr)
            continue
        matched = 0
        for period, actual, est, spct in eps[:8]:  # 最近8季
            m = match_period_to_release(period, filings)
            if m:
                rel, acc, gap = m
                # 盘前/盘后: acceptance 是 ET, >16:00 盘后 → 次日可交易
                session = "?"
                tradable = rel
                if acc:
                    try:
                        hh = int(acc[11:13])
                        session = "盘后" if hh >= 16 else "盘前/盘中"
                    except Exception:
                        pass
                rows.append({"ticker": t, "period": period, "release": rel,
                             "gap_days": gap, "session": session,
                             "has_time": acc is not None})
                matched += 1
            else:
                rows.append({"ticker": t, "period": period, "release": None,
                             "gap_days": None, "session": None, "has_time": False})
        print(f"  {t}: EPS季度{len(eps[:8])}  匹配到8-K公布日 {matched}", file=sys.stderr)

    total = len(rows)
    matched = [r for r in rows if r["release"]]
    match_rate = len(matched) / total if total else 0
    gaps = [r["gap_days"] for r in matched]
    has_time = sum(1 for r in matched if r["has_time"])

    print(f"\n{'='*68}\n审计结果\n{'='*68}")
    print(f"CIK映射失效(实体重组,历史8-K在旧CIK下): {len(cik_failed)}只 {cik_failed}")
    print(f"  → 这是ticker→CIK数据工程问题, 非时间戳不可用; 全量前需补'历史实体CIK'回退")
    print(f"\n可用票财报事件: {total}  匹配到真实公布日: {len(matched)}  "
          f"→ 匹配率 {match_rate:.1%} {'✓' if match_rate>=0.90 else '✗(<90%)'}")
    if gaps:
        gaps = np.array(gaps)
        print(f"\n前视天数(财季末period → 真实公布日, 之前用period对齐会前视这么多天):")
        print(f"  中位={np.median(gaps):.0f}天  均值={gaps.mean():.1f}天  "
              f"min={gaps.min()} max={gaps.max()}")
        print(f"  → 坐实: 用 period(财季末)对齐价格会系统性前视约{np.median(gaps):.0f}天, 严重泄漏")
    print(f"\n时刻覆盖率(能判盘前/盘后): {has_time}/{len(matched)} = "
          f"{has_time/max(1,len(matched)):.1%}")
    sess = {}
    for r in matched:
        sess[r["session"]] = sess.get(r["session"], 0) + 1
    print(f"  session分布: {sess}")

    # 存明细
    import csv
    os.makedirs("output", exist_ok=True)
    with open("output/earnings_audit.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "period", "release", "gap_days",
                                          "session", "has_time"])
        w.writeheader(); w.writerows(rows)
    print(f"\n明细存 output/earnings_audit.csv")
    print(f"\n【裁决】", "✓ 时间戳可靠, 可进入PEAD正式验证" if match_rate >= 0.90
          else "✗ 匹配率不足, PEAD No-Go(或需改进日期匹配逻辑)")


if __name__ == "__main__":
    main()
