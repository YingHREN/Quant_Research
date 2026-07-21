"""非线性模型训练：用 GBDT 学习因子→前向收益的非单调关系。

对比专家加权分(score)与 GBDT 的横截面 Rank IC。
严格 walk-forward: 按日期时间序切分,训练集只用测试日之前的样本,
并 purge 掉跨界的 HORIZON 天标签,避免未来泄漏。

用法: python train_model.py --data output/bt_features.csv
"""
from __future__ import annotations

import argparse
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

FEATURES = [
    "rs", "adr_pct", "pct_from_high", "pct_above_low",
    "ext_ema10", "ext_ema20", "ext_ema50",
    "vcp_n", "vcp_decr", "vcp_voldry", "vcp_tight",
    "vcp_quality", "vcp_slope", "vcp_lfr", "vcp_volslope", "vcp_baselen",
    "vol_ratio", "pct_over_pivot", "breakout", "pocket_pivot",
    "overheat", "ret5_atr", "ret10_atr", "run20", "atr_pctile", "consec_up",
    "market_ok",
]


def spearman(a, b):
    ar = pd.Series(a).rank().values
    br = pd.Series(b).rank().values
    if np.std(ar) == 0 or np.std(br) == 0:
        return np.nan
    return float(np.corrcoef(ar, br)[0, 1])


def daily_rank_ic(df, score_col, ret_col="fwd_ret"):
    ics = []
    for d, g in df.groupby("date"):
        if len(g) >= 5:
            ic = spearman(g[score_col].values, g[ret_col].values)
            if not np.isnan(ic):
                ics.append(ic)
    a = np.array(ics)
    return {"mean_ic": a.mean(), "icir": a.mean() / a.std() if a.std() > 0 else 0,
            "pos_rate": (a > 0).mean(), "n_days": len(a)}


def walk_forward_gbdt(df, n_splits=5, embargo_days=40):
    """按日期时间序做 walk-forward,输出 GBDT 的 OOF 预测。"""
    df = df.sort_values("date").reset_index(drop=True)
    dates = sorted(df["date"].unique())
    fold_edges = np.linspace(0, len(dates), n_splits + 1, dtype=int)

    df["gbdt_pred"] = np.nan
    for k in range(1, n_splits):
        train_end = dates[fold_edges[k] - 1]
        test_start_idx = fold_edges[k]
        test_end_idx = fold_edges[k + 1]
        if test_end_idx > len(dates):
            test_end_idx = len(dates)
        test_dates = dates[test_start_idx:test_end_idx]
        if not test_dates:
            continue
        # 训练集: train_end 之前; embargo: 掐掉临近 test 的 40 天
        train = df[df["date"] <= train_end]
        # 简单 embargo: 去掉训练集最后 embargo_days 个交易日
        cutoff_dates = sorted(train["date"].unique())[:-embargo_days] if len(sorted(train["date"].unique())) > embargo_days else []
        if cutoff_dates:
            train = train[train["date"].isin(cutoff_dates)]
        test = df[df["date"].isin(test_dates)]
        if len(train) < 200 or len(test) < 20:
            continue

        Xtr = train[FEATURES].fillna(train[FEATURES].median()).values
        ytr = train["fwd_ret"].values
        Xte = test[FEATURES].fillna(train[FEATURES].median()).values

        model = GradientBoostingRegressor(
            n_estimators=150, max_depth=3, learning_rate=0.03,
            subsample=0.8, min_samples_leaf=50, random_state=42)
        model.fit(Xtr, ytr)
        df.loc[test.index, "gbdt_pred"] = model.predict(Xte)
    return df, model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="output/bt_features.csv")
    args = ap.parse_args()
    df = pd.read_csv(args.data)
    df = df.sort_values("date").reset_index(drop=True)
    print(f"样本 {len(df)}  股票 {df['ticker'].nunique()}  日期 {df['date'].nunique()}")

    # === 锁箱期分割: 前70%研发,后30%只验证一次(治过拟合) ===
    dates = sorted(df["date"].unique())
    split = dates[int(len(dates) * 0.7)]
    dev = df[df["date"] < split].copy()
    lock = df[df["date"] >= split].copy()
    print(f"研发期 <{split}: {len(dev)}样本  |  锁箱期 >={split}: {len(lock)}样本")

    # 基线: 专家加权分(分别在研发/锁箱期看)
    print("\n=== 基线: 专家加权分 Rank IC ===")
    for name, d in [("研发期", dev), ("锁箱期", lock)]:
        b = daily_rank_ic(d, "score")
        print(f"  {name}: 均值={b['mean_ic']:+.4f} ICIR={b['icir']:+.3f} IC>0={b['pos_rate']:.1%} (日{b['n_days']})")

    # === GBDT: 只用研发期训练,锁箱期预测(真正样本外) ===
    med = dev[FEATURES].median()
    Xdev = dev[FEATURES].fillna(med).values
    ydev = dev["fwd_ret"].values
    Xlock = lock[FEATURES].fillna(med).values

    from sklearn.ensemble import GradientBoostingRegressor
    model = GradientBoostingRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.03,
        subsample=0.8, min_samples_leaf=50, random_state=42)
    model.fit(Xdev, ydev)

    dev["gbdt_pred"] = model.predict(Xdev)
    lock["gbdt_pred"] = model.predict(Xlock)

    print("\n=== GBDT 非线性模型 Rank IC ===")
    d_ic = daily_rank_ic(dev, "gbdt_pred")
    l_ic = daily_rank_ic(lock, "gbdt_pred")
    print(f"  研发期(样本内): 均值={d_ic['mean_ic']:+.4f} ICIR={d_ic['icir']:+.3f} IC>0={d_ic['pos_rate']:.1%}")
    print(f"  锁箱期(样本外!): 均值={l_ic['mean_ic']:+.4f} ICIR={l_ic['icir']:+.3f} IC>0={l_ic['pos_rate']:.1%} (日{l_ic['n_days']})")
    print("  → 锁箱期IC才算数。若≈0或为负,说明GBDT也没学到可外推信号")

    # 特征重要性
    imp = sorted(zip(FEATURES, model.feature_importances_), key=lambda x: -x[1])
    print("\nGBDT 特征重要性 Top12:")
    for name, v in imp[:12]:
        print(f"  {name:16s} {v:.3f}")

    # 锁箱期 GBDT 预测五分位 → 实际上涨率
    lock["actual_up"] = lock["label"] == "上涨"
    if len(lock) >= 25:
        lock["q"] = pd.qcut(lock["gbdt_pred"].rank(method="first"), 5,
                            labels=["Q1低", "Q2", "Q3", "Q4", "Q5高"])
        print("\n锁箱期 GBDT预测五分位 → 实际上涨率:")
        for q, gg in lock.groupby("q", observed=True):
            print(f"  {q}: n={len(gg):>4}  上涨率={gg['actual_up'].mean():.1%}  "
                  f"平均收益={gg['fwd_ret'].mean():+.2f}%  中位={gg['fwd_ret'].median():+.2f}%")
        # 关键: Q5高分组是否真的比Q1低分组上涨率高
        q5 = lock[lock["q"] == "Q5高"]["actual_up"].mean()
        q1 = lock[lock["q"] == "Q1低"]["actual_up"].mean()
        print(f"\n  【裁定】锁箱期 Q5高-Q1低 上涨率差 = {(q5-q1)*100:+.1f}个点  "
              f"{'✓模型有效' if q5-q1 > 0.05 else '✗模型无区分力'}")


if __name__ == "__main__":
    main()
