"""P1#12: 用可解释 EBM 替代专家加权,严格按 Codex 决策者的预注册验证协议裁决。

核心纪律(Codex 指令):
- 主模型 = ExplainableBoostingRegressor(interactions=0, 纯可加形状,可审计)
- 对照 = 专家分(旧基线) + HistGBDT(过拟合探针,非生产)
- 嵌套 purged walk-forward: 外层OOS出预测, 内层选小网格超参; fold间挖>=40日embargo
- 三模型完全同口径同样本, 汇总OOF后过三道硬门槛
- 【已被看过的锁箱期(bt_p1的后30%)已失去锁箱资格】, 这里只做研发期OOS裁决

放行门槛(全部同时满足才算"真信号"):
  1. 原始 Rank IC: 块bootstrap 95%CI下界>0 且 点估计>=+0.03
  2. beta中性化 Rank IC: 同上
  3. 26相位非重叠: >=18/26相位为正 且 中位>+0.02
  4. Q5-Q1收益差: 块bootstrap 95%CI下界>0 且 Q5中位/胜率同方向
未过 → 记录"可解释非线性模型同样无稳定alpha", 关闭预测型选股路线(也算P1#12成功)。

用法: ./venv/bin/python train_model_v2.py --data output/bt_p1.csv
"""
from __future__ import annotations

import argparse
import json
import warnings
import numpy as np
import pandas as pd

# ============ 预注册配置(运行前冻结,防多重尝试作弊) ============
PREREG = {
    "features": [
        "rs", "adr_pct", "pct_from_high", "pct_above_low",
        "ext_ema10", "ext_ema20", "ext_ema50",
        "vcp_n", "vcp_decr", "vcp_voldry", "vcp_tight",
        "vcp_quality", "vcp_slope", "vcp_lfr", "vcp_volslope", "vcp_baselen",
        "vol_ratio", "pct_over_pivot", "breakout", "pocket_pivot",
        "overheat", "ret5_atr", "ret10_atr", "run20", "atr_pctile", "consec_up",
        "market_ok",
    ],
    "target": "fwd_ret",
    "horizon": 40,
    "step": 5,
    "embargo_days": 40,          # fold间挖除的交易日窗口(标签期长度)
    "n_outer_folds": 5,
    "ebm_grid": [                # 内层选择用的小网格(全部计入模型选择)
        {"max_bins": 128, "learning_rate": 0.01, "max_rounds": 3000, "min_samples_leaf": 50},
        {"max_bins": 64,  "learning_rate": 0.02, "max_rounds": 2000, "min_samples_leaf": 80},
    ],
    "gates": {
        "ic_point_min": 0.03,
        "phase_pos_min": 18,     # /26
        "phase_median_min": 0.02,
        "block_len": 40,
    },
    "seed": 42,
}
FEATURES = PREREG["features"]
HORIZON = PREREG["horizon"]
STEP = PREREG["step"]


def spearman(a, b):
    ar = pd.Series(a).rank().values
    br = pd.Series(b).rank().values
    if np.std(ar) == 0 or np.std(br) == 0:
        return np.nan
    return float(np.corrcoef(ar, br)[0, 1])


def daily_ic_series(df, score_col, ret_col):
    out = []
    for d, g in df.groupby("date"):
        if len(g) >= 5:
            ic = spearman(g[score_col].values, g[ret_col].values)
            if not np.isnan(ic):
                out.append((d, ic))
    out.sort(key=lambda x: x[0])
    return out


def block_bootstrap_ci(vals, block=40, n_boot=2000, seed=42):
    a = np.asarray(vals, dtype=float)
    if len(a) < block:
        # 样本太短: 退化为普通bootstrap(仍给个粗CI)
        block = max(2, len(a) // 4)
        if block < 2:
            return None
    n = len(a)
    rng = np.random.RandomState(seed)
    n_blocks = int(np.ceil(n / block))
    means = []
    for _ in range(n_boot):
        starts = rng.randint(0, n - block + 1, size=n_blocks)
        sample = np.concatenate([a[s:s + block] for s in starts])[:n]
        means.append(sample.mean())
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def neutralize_score(df, score_col):
    """把评分对 adr_pct(beta代理)残差化, 返回新列名。"""
    df = df.copy()
    out_col = score_col + "_neu"

    def resid(g):
        x = g["adr_pct"].astype(float).values
        y = g[score_col].astype(float).values
        m = np.isfinite(x) & np.isfinite(y)
        if m.sum() < 5 or np.std(x[m]) == 0:
            g[out_col] = g[score_col]
            return g
        b = np.polyfit(x[m], y[m], 1)
        g[out_col] = y - np.polyval(b, x)
        return g

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        df = df.groupby("date", group_keys=False).apply(resid)
    return df, out_col


def multiphase_positive(df, score_col, ret_col, horizon=HORIZON):
    """26相位非重叠: 返回(相位IC均值列表)。"""
    dates = sorted(df["date"].unique())
    phase_means = []
    for phase in range(min(horizon, len(dates))):
        keep = dates[phase::horizon]
        if len(keep) < 2:
            continue
        sub = df[df["date"].isin(keep)]
        ics = daily_ic_series(sub, score_col, ret_col)
        if ics:
            phase_means.append(np.mean([x[1] for x in ics]))
    return phase_means


# ============ 嵌套 purged walk-forward ============
def purged_walkforward(df, model_factory, grid, embargo_days, n_folds, seed):
    """外层OOS出预测, 内层从grid选超参。返回带 oof_pred 列的df副本。

    embargo: 训练集用 test_start 之前的数据, 但掐掉临近 test 的 embargo 天
    (它们的HORIZON标签窗口会探入test)。每fold重新fit。
    """
    df = df.sort_values("date").reset_index(drop=True)
    dates = sorted(df["date"].unique())
    edges = np.linspace(0, len(dates), n_folds + 1, dtype=int)
    df = df.copy()
    df["oof_pred"] = np.nan
    embargo_steps = max(1, embargo_days // STEP)

    for k in range(1, n_folds):
        test_dates = dates[edges[k]:edges[k + 1]]
        if not test_dates:
            continue
        train_dates_all = dates[:edges[k]]
        if len(train_dates_all) <= embargo_steps:
            continue
        train_dates = train_dates_all[:-embargo_steps]  # 挖embargo
        train = df[df["date"].isin(train_dates)]
        test = df[df["date"].isin(test_dates)]
        if len(train) < 300 or len(test) < 20:
            continue

        med = train[FEATURES].median()
        Xtr = train[FEATURES].fillna(med).values
        ytr = train[PREREG["target"]].values
        Xte = test[FEATURES].fillna(med).values

        # 内层: 在train内再切一个validation尾段选grid(简化: 用train末20%)
        best_hp, best_ic = grid[0], -1e9
        if len(grid) > 1:
            vdates = train_dates[-max(3, len(train_dates) // 5):]
            inner_tr = train[~train["date"].isin(vdates)]
            inner_va = train[train["date"].isin(vdates)]
            if len(inner_tr) > 200 and len(inner_va) >= 20:
                imed = inner_tr[FEATURES].median()
                iXtr = inner_tr[FEATURES].fillna(imed).values
                iytr = inner_tr[PREREG["target"]].values
                for hp in grid:
                    m = model_factory(hp, seed)
                    m.fit(iXtr, iytr)
                    va = inner_va.copy()
                    va["p"] = m.predict(inner_va[FEATURES].fillna(imed).values)
                    ics = daily_ic_series(va, "p", PREREG["target"])
                    ic = np.mean([x[1] for x in ics]) if ics else -1e9
                    if ic > best_ic:
                        best_ic, best_hp = ic, hp

        model = model_factory(best_hp, seed)
        model.fit(Xtr, ytr)
        df.loc[test.index, "oof_pred"] = model.predict(Xte)
    return df


def make_ebm(hp, seed):
    from interpret.glassbox import ExplainableBoostingRegressor
    return ExplainableBoostingRegressor(
        interactions=0, max_bins=hp["max_bins"], learning_rate=hp["learning_rate"],
        max_rounds=hp["max_rounds"], min_samples_leaf=hp["min_samples_leaf"],
        random_state=seed)


def make_hgb(hp, seed):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(
        max_iter=300, max_depth=3, learning_rate=0.03,
        min_samples_leaf=50, l2_regularization=1.0, random_state=seed)


# ============ 裁决 ============
def adjudicate(df, pred_col, name):
    """对某预测列跑三道硬门槛, 返回 verdict dict + 打印。"""
    print(f"\n{'='*66}\n【{name}】 pred_col={pred_col}\n{'='*66}")
    g = PREREG["gates"]
    res = {"name": name}

    # 1) 原始 Rank IC
    raw = daily_ic_series(df, pred_col, PREREG["target"])
    raw_ic = np.mean([x[1] for x in raw]) if raw else np.nan
    ci = block_bootstrap_ci([x[1] for x in raw], block=g["block_len"])
    res["raw_ic"] = raw_ic
    res["raw_ci"] = ci
    pass_raw = ci is not None and ci[0] > 0 and raw_ic >= g["ic_point_min"]
    print(f"1) 原始 Rank IC = {raw_ic:+.4f}  95%CI=[{ci[0]:+.4f},{ci[1]:+.4f}]  "
          f"{'✓' if pass_raw else '✗'}(需CI下界>0且点估>={g['ic_point_min']})")

    # 2) beta中性化
    dfn, ncol = neutralize_score(df, pred_col)
    neu = daily_ic_series(dfn, ncol, PREREG["target"])
    neu_ic = np.mean([x[1] for x in neu]) if neu else np.nan
    ci_n = block_bootstrap_ci([x[1] for x in neu], block=g["block_len"])
    res["neu_ic"] = neu_ic
    res["neu_ci"] = ci_n
    pass_neu = ci_n is not None and ci_n[0] > 0 and neu_ic >= g["ic_point_min"]
    print(f"2) 中性化 Rank IC = {neu_ic:+.4f}  95%CI=[{ci_n[0]:+.4f},{ci_n[1]:+.4f}]  "
          f"{'✓' if pass_neu else '✗'}")

    # 3) 多相位
    pm = multiphase_positive(df, pred_col, PREREG["target"])
    n_pos = sum(1 for x in pm if x > 0)
    med = np.median(pm) if pm else np.nan
    res["phase_pos"] = f"{n_pos}/{len(pm)}"
    res["phase_median"] = med
    # 门槛按比例: >=70%相位为正(而非硬编码26, 因OOF样本相位数会变) 且 中位>阈值
    frac_min = g["phase_pos_min"] / 26.0
    pass_phase = len(pm) > 0 and (n_pos / len(pm)) >= frac_min and med > g["phase_median_min"]
    print(f"3) 多相位: {n_pos}/{len(pm)}相位为正({100*n_pos/max(1,len(pm)):.0f}%)  中位={med:+.4f}  "
          f"{'✓' if pass_phase else '✗'}(需>={100*frac_min:.0f}%且中位>{g['phase_median_min']})")

    # 4) Q5-Q1
    d = df.dropna(subset=[pred_col]).copy()
    pass_q = False
    if len(d) >= 25:
        d["q"] = pd.qcut(d[pred_col].rank(method="first"), 5, labels=False)
        d["actual_up"] = d["label"] == "上涨"
        q5, q1 = d[d["q"] == 4], d[d["q"] == 0]
        # Q5-Q1 每日收益差的块bootstrap
        diff_ret = q5[PREREG["target"]].mean() - q1[PREREG["target"]].mean()
        q5_med, q1_med = q5[PREREG["target"]].median(), q1[PREREG["target"]].median()
        q5_win, q1_win = q5["actual_up"].mean(), q1["actual_up"].mean()
        # 简化CI: 对Q5、Q1各日均收益差做bootstrap
        merged = d[d["q"].isin([0, 4])]
        daily_diff = []
        for dt, gg in merged.groupby("date"):
            a5 = gg[gg["q"] == 4][PREREG["target"]]
            a1 = gg[gg["q"] == 0][PREREG["target"]]
            if len(a5) and len(a1):
                daily_diff.append(a5.mean() - a1.mean())
        ci_q = block_bootstrap_ci(daily_diff, block=g["block_len"]) if len(daily_diff) >= 4 else None
        same_dir = (diff_ret > 0) and (q5_med > q1_med) and (q5_win > q1_win)
        pass_q = ci_q is not None and ci_q[0] > 0 and same_dir
        res["q5_q1_ret"] = diff_ret
        res["q5_q1_ci"] = ci_q
        print(f"4) Q5-Q1均收益差={diff_ret:+.2f}%  中位差={q5_med-q1_med:+.2f}%  胜率差={100*(q5_win-q1_win):+.1f}点  "
              f"CI={('[%.2f,%.2f]'%ci_q) if ci_q else 'NA'}  {'✓' if pass_q else '✗'}(需CI>0且中位/胜率同方向)")

    all_pass = pass_raw and pass_neu and pass_phase and pass_q
    res["verdict"] = "PASS" if all_pass else "FAIL"
    print(f"\n  ►► 【{name} 裁决】: {res['verdict']}  "
          f"(原始{'✓' if pass_raw else '✗'} 中性{'✓' if pass_neu else '✗'} "
          f"相位{'✓' if pass_phase else '✗'} Q5Q1{'✓' if pass_q else '✗'})")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="output/bt_p1.csv")
    ap.add_argument("--save-shapes", default="output/ebm_shapes.json",
                    help="保存EBM跨fold形状图(供后续改进买卖引擎)")
    args = ap.parse_args()

    df = pd.read_csv(args.data)
    df = df.sort_values("date").reset_index(drop=True)
    print(f"样本 {len(df)}  股票 {df['ticker'].nunique()}  独立决策日 {df['date'].nunique()}")
    print(f"预注册配置已冻结: {len(FEATURES)}特征, embargo={PREREG['embargo_days']}日, "
          f"{PREREG['n_outer_folds']}外层fold")

    # 专家分基线(全研发期OOS口径: 专家分是固定映射, 无需fit, 直接用score)
    df["expert"] = df["score"]

    # EBM 与 HistGBDT: 嵌套 purged walk-forward 出 OOF
    print("\n跑 EBM 嵌套 purged walk-forward ...")
    df_ebm = purged_walkforward(df, make_ebm, PREREG["ebm_grid"],
                                PREREG["embargo_days"], PREREG["n_outer_folds"], PREREG["seed"])
    df["ebm_oof"] = df_ebm["oof_pred"]

    print("跑 HistGBDT(过拟合探针) ...")
    df_hgb = purged_walkforward(df, make_hgb, [PREREG["ebm_grid"][0]],
                                PREREG["embargo_days"], PREREG["n_outer_folds"], PREREG["seed"])
    df["hgb_oof"] = df_hgb["oof_pred"]

    # 只在三者都有OOF预测的样本上同口径裁决
    common = df.dropna(subset=["ebm_oof", "hgb_oof"]).copy()
    print(f"\n三模型同口径样本: {len(common)}  (OOF覆盖的test folds)")

    verdicts = []
    verdicts.append(adjudicate(common, "expert", "专家分(基线)"))
    verdicts.append(adjudicate(common, "ebm_oof", "EBM(主模型,interactions=0)"))
    verdicts.append(adjudicate(common, "hgb_oof", "HistGBDT(过拟合探针)"))

    # 汇总裁决
    print(f"\n{'='*66}\n最终裁决汇总\n{'='*66}")
    for v in verdicts:
        print(f"  {v['name']:28s} {v['verdict']}  "
              f"原始IC={v.get('raw_ic',float('nan')):+.4f} 中性IC={v.get('neu_ic',float('nan')):+.4f} "
              f"相位={v.get('phase_pos','NA')}")
    ebm_v = next(v for v in verdicts if v["name"].startswith("EBM"))
    if ebm_v["verdict"] == "PASS":
        print("\n✓ EBM 过全部硬门槛 → 可进 shadow mode(先不替换V2, 累计新数据再决定)")
    else:
        print("\n✗ EBM 未过硬门槛 → 【正式记录】可解释非线性模型同样无稳定横截面alpha。")
        print("  按Codex裁定: 关闭预测型选股路线, 不再调参挖结果。这也算P1#12成功完成。")
        print("  下一步价值: 提取跨fold稳定的EBM形状图 → 只用于改进买卖引擎(仓位/买入禁区/止盈),")
        print("  且每条规则须单独消融回测+过成本/回撤/时间稳定性验证。")

    # 保存全样本EBM形状(供后续形状分析; 注意这是全样本拟合仅供研究, 非上线证据)
    try:
        from interpret.glassbox import ExplainableBoostingRegressor
        med = df[FEATURES].median()
        X = df[FEATURES].fillna(med).values
        y = df[PREREG["target"]].values
        full = ExplainableBoostingRegressor(interactions=0, random_state=PREREG["seed"])
        full.fit(X, y)
        expl = full.explain_global()
        shapes = {}
        data = expl.data()
        for i, fname in enumerate(FEATURES):
            fd = expl.data(i)
            if fd and "names" in fd and "scores" in fd:
                shapes[fname] = {"bins": list(map(float, fd["names"])),
                                 "scores": list(map(float, fd["scores"]))}
        with open(args.save_shapes, "w") as f:
            json.dump(shapes, f, ensure_ascii=False, indent=1)
        print(f"\nEBM全样本形状图已存 {args.save_shapes} ({len(shapes)}个特征, 仅供研究)")
    except Exception as e:
        print(f"\n形状图保存跳过: {e}")


if __name__ == "__main__":
    main()
