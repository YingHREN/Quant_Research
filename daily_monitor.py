"""每日监控 —— 拉最新数据, 存数据库, 输出【客观技术状态】(不做涨跌预测)。

诚实边界(本项目铁律): 选股/买卖点预测已五次证伪, lift≈1.0。本工具只报可观察的事实:
趋势位置(MA10/50/200)、距pivot、VCP形态候选、过热分、以及"确认转强"的客观价位清单。
绝不输出涨跌概率/买卖信号。

维护: watchlist.txt(每行一个ticker, 随时增减)。数据: SQLite output/monitor.db。
每次跑对比"上次快照", 报状态变化(如某票今日站上MA50)。

用法:
  ./venv/bin/python daily_monitor.py            # 拉数据+存库+出报告
  ./venv/bin/python daily_monitor.py --no-fetch # 用缓存(不联网), 快速重出报告
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import datetime as dt

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data.fetch import fetch
from factors.compute import vcp_analysis, pivot_breakout, overheat, tight_platform

DB = os.path.join(os.path.dirname(__file__), "output", "monitor.db")
WATCHLIST = os.path.join(os.path.dirname(__file__), "watchlist.txt")


def load_watchlist():
    if not os.path.exists(WATCHLIST):
        return []
    with open(WATCHLIST) as f:
        return [ln.strip().upper() for ln in f if ln.strip() and not ln.startswith("#")]


def tech_state(h):
    """算一只票的客观技术状态 dict。"""
    c = h["Close"]; v = h["Volume"]; hi = h["High"]
    px = float(c.iloc[-1])
    ma10 = float(c.iloc[-10:].mean())
    ma20 = float(c.iloc[-20:].mean())
    ma50 = float(c.iloc[-50:].mean()) if len(c) >= 50 else None
    ma200 = float(c.iloc[-200:].mean()) if len(c) >= 200 else None
    ma50_prev = float(c.iloc[-60:-10].mean()) if len(c) >= 60 else None
    hi52 = float(c.iloc[-252:].max()) if len(c) >= 252 else float(c.max())
    r20 = (px / c.iloc[-21] - 1) * 100 if len(c) > 21 else None
    avg50v = float(v.iloc[-50:].mean()) if len(v) >= 50 else float(v.mean())
    vol5 = float(v.iloc[-5:].mean())
    resist20 = float(hi.iloc[-20:].max())

    v_vcp = vcp_analysis(h)
    p = pivot_breakout(h)
    oh = overheat(h)
    plat = tight_platform(h)

    # 趋势分级(客观描述)
    if ma50 and ma200:
        if px > ma50 and ma50 > ma200:
            trend = "上升(价>MA50>MA200)"
        elif px > ma50:
            trend = "转强中(价>MA50, 但MA50<MA200)"
        elif px > ma20 and ma10 > ma20:
            trend = "短期反弹(价>MA20,未站MA50)"
        elif px < ma50 and ma50 < ma200:
            trend = "下降(价<MA50<MA200)"
        else:
            trend = "震荡/回调"
    else:
        trend = "历史不足"

    return {
        "price": round(px, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
        "ma50": round(ma50, 2) if ma50 else None,
        "ma200": round(ma200, 2) if ma200 else None,
        "ma50_slope_up": bool(ma50 and ma50_prev and ma50 > ma50_prev),
        "trend": trend,
        "pct_from_high": round((1 - px / hi52) * 100, 1),
        "r20": round(r20, 1) if r20 is not None else None,
        "above_ma50": bool(ma50 and px > ma50),
        "dist_ma50_pct": round((ma50 / px - 1) * 100, 1) if ma50 else None,  # 还差多少站上
        "vol5_ratio": round(vol5 / avg50v, 2) if avg50v else None,
        "resist20": round(resist20, 2),
        "vcp_ok": v_vcp["reject_reason"] is None,
        "vcp_quality": v_vcp["vcp_quality"],
        "platform_ok": plat["is_platform"],
        "platform_range": plat.get("range_pct"),
        "vcp_reject": v_vcp["reject_reason"],
        "vcp_pivot": v_vcp["vcp_pivot"],
        "vcp_extended": v_vcp.get("is_extended", False),
        "pivot": p.get("pivot"),
        "pct_over_pivot": p.get("pct_over_pivot"),
        "breakout": bool(p.get("breakout")),
        "overheat": oh["overheat_score"],
    }


def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS snapshots(
        date TEXT, ticker TEXT, price REAL, trend TEXT, above_ma50 INT,
        ma50 REAL, ma200 REAL, ma50_slope_up INT, pct_from_high REAL, r20 REAL,
        dist_ma50_pct REAL, vol5_ratio REAL, resist20 REAL,
        vcp_ok INT, vcp_quality REAL, vcp_reject TEXT, vcp_pivot REAL,
        vcp_extended INT, pivot REAL, pct_over_pivot REAL, breakout INT, overheat REAL,
        PRIMARY KEY(date, ticker))""")
    con.commit()
    return con


def last_snapshot(con, ticker, before_date):
    cur = con.execute("SELECT date,trend,above_ma50,breakout,vcp_ok FROM snapshots "
                      "WHERE ticker=? AND date<? ORDER BY date DESC LIMIT 1",
                      (ticker, before_date))
    return cur.fetchone()


def save(con, date, ticker, s):
    con.execute("""INSERT OR REPLACE INTO snapshots VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (date, ticker, s["price"], s["trend"], int(s["above_ma50"]),
         s["ma50"], s["ma200"], int(s["ma50_slope_up"]), s["pct_from_high"], s["r20"],
         s["dist_ma50_pct"], s["vol5_ratio"], s["resist20"],
         int(s["vcp_ok"]), s["vcp_quality"], s["vcp_reject"], s["vcp_pivot"],
         int(s["vcp_extended"]), s["pivot"], s["pct_over_pivot"], int(s["breakout"]),
         s["overheat"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="用缓存不联网")
    args = ap.parse_args()

    wl = load_watchlist()
    if not wl:
        print("watchlist.txt 为空, 请添加ticker(每行一个)"); return
    con = init_db()

    print("=" * 78)
    print(f"每日监控  watchlist {len(wl)}只  {'(用缓存)' if args.no_fetch else '(拉最新)'}")
    print("⚠️ 仅客观技术状态, 不含涨跌预测/买卖信号(本系统预测能力已证伪)")
    print("=" * 78)

    results = []
    data_date = None
    for t in wl:
        sd = fetch(t)
        if not sd.ok:
            print(f"  {t}: 数据失败 {sd.error[:40]}", file=sys.stderr); continue
        h = sd.history
        s = tech_state(h)
        d = str(h.index[-1].date())
        data_date = d
        prev = last_snapshot(con, t, d)
        save(con, d, t, s)
        results.append((t, s, prev))
    con.commit()

    # 报告: 按趋势强弱分组
    order = {"上升(价>MA50>MA200)": 0, "转强中(价>MA50, 但MA50<MA200)": 1,
             "短期反弹(价>MA20,未站MA50)": 2, "震荡/回调": 3, "下降(价<MA50<MA200)": 4}
    results.sort(key=lambda x: order.get(x[1]["trend"], 9))

    print(f"\n数据日期: {data_date}\n")
    for t, s, prev in results:
        # 状态变化提示
        change = ""
        if prev:
            _, ptrend, pabove, pbreak, pvcp = prev
            if pabove != int(s["above_ma50"]):
                change += " ⚡" + ("今日站上MA50" if s["above_ma50"] else "今日跌破MA50")
            if pbreak != int(s["breakout"]) and s["breakout"]:
                change += " ⚡今日突破pivot"
            if pvcp != int(s["vcp_ok"]) and s["vcp_ok"]:
                change += " ⚡今日成VCP形态候选"
        if s["vcp_ok"]:
            vcp_str = f"VCP✓质{s['vcp_quality']}{'(已延伸)' if s['vcp_extended'] else ''}"
        elif s.get("platform_ok"):
            vcp_str = f"紧凑平台✓(区间{s['platform_range']}%,接近突破)"
        else:
            vcp_str = "非VCP"
        print(f"● {t:<6} ${s['price']:<8.1f} {s['trend']}{change}")
        ma50_desc = (f"高于MA50 {-s['dist_ma50_pct']:+.1f}%" if s["above_ma50"]
                     else f"需+{s['dist_ma50_pct']:.1f}%站上MA50")
        print(f"    距52周高 {s['pct_from_high']:+.1f}%  {ma50_desc}  MA50{'↑' if s['ma50_slope_up'] else '↓'}"
              f"  近20日{s['r20']:+.1f}%  量比{s['vol5_ratio']}")
        print(f"    {vcp_str}  pivot ${s['pivot']}  距pivot {s['pct_over_pivot']}%"
              f"  过热{s['overheat']}/100")
        # 确认转强价位清单(对未站上MA50的票)
        if not s["above_ma50"] and s["ma50"]:
            print(f"    ▸确认观察: 收盘站上MA50 ${s['ma50']}(需{s['dist_ma50_pct']:+.1f}%) + "
                  f"放量(量比>1.0) + 越过近20日高 ${s['resist20']}")
        print()

    print("提示: 改 watchlist.txt 增减股票; 再跑 './venv/bin/python daily_monitor.py' 更新")
    print("⚠️ 仅供研究, 非投资建议。'确认观察'是技术分析事实点, 非预测——本系统无验证过的预测力。")


if __name__ == "__main__":
    main()
