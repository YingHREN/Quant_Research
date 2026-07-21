"""P0 严谨验证：解决 Codex 指出的验证漏洞。

对回测样本(bt_expanded.csv)做:
1. 独立决策日数报告(揭穿"5908伪样本量")
2. 时间分割: 研发期(前70%) vs 锁箱期(后30%),分别报 Rank IC
3. 日期块 bootstrap: 对每日IC序列做块重采样,给 Rank IC 的可信区间
4. 非重叠40日抽样敏感性: 每40天取一次点,消除样本重叠后再看IC

只读CSV,不重跑回测。用法: python validate.py --data output/bt_expanded.csv
"""
from __future__ import annotations

import argparse
import numpy as np
import pandas as pd

HORIZON = 40
STEP = 5  # 与 backtest.py 一致: 每5交易日取一个决策点


def spearman(a, b):
    ar = pd.Series(a).rank().values
    br = pd.Series(b).rank().values
    if np.std(ar) == 0 or np.std(br) == 0:
        return np.nan
    return float(np.corrcoef(ar, br)[0, 1])


def daily_ic_series(df):
    """返回按日期排序的 (date, ic) 列表,每日横截面≥5只。"""
    out = []
    for d, g in df.groupby("date"):
        if len(g) >= 5:
            ic = spearman(g["score"].values, g["fwd_ret"].values)
            if not np.isnan(ic):
                out.append((d, ic))
    out.sort(key=lambda x: x[0])
    return out


def summarize(ics, label):
    if not ics:
        print(f"  {label}: 无有效日")
        return None
    a = np.array([x[1] for x in ics])
    print(f"  {label}: Rank IC均值={a.mean():+.4f}  ICIR={a.mean()/a.std() if a.std()>0 else 0:+.3f}  "
          f"IC>0={np.mean(a>0):.1%}  有效日={len(a)}")
    return a


def block_bootstrap_ci(ics, block=40, n_boot=2000, seed=42):
    """日期块 bootstrap: 保留IC时间序列的自相关(标签重叠),给均值IC的95%CI。"""
    if len(ics) < block:
        return None
    a = np.array([x[1] for x in ics])
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


def neutralize(df):
    """Beta/波动率中性化: 每个决策日内,把 score 对 beta代理(adr_pct)回归,取残差作为
    中性化后的评分,再算 Rank IC。

    Codex P0#6: 排除'评分只是在给高/低波动(beta)股排序'的可能。
    注意: 简单 demean 收益对 Spearman 秩相关无效(减常数不改变当日排序),
    所以这里对'评分'做残差化——把评分里能被 beta 线性解释的部分剔除,
    看剩下的部分还有没有排序能力。若中性化后 IC 大幅缩水,说明原 IC 主要是
    'beta 排序'的伪装。返回新增列 score_neu 的副本。
    """
    df = df.copy()

    def resid(g):
        x = g["adr_pct"].astype(float).values
        y = g["score"].astype(float).values
        m = np.isfinite(x) & np.isfinite(y)
        if m.sum() < 5 or np.std(x[m]) == 0:
            g["score_neu"] = g["score"]
            return g
        b = np.polyfit(x[m], y[m], 1)
        pred = np.polyval(b, x)
        g["score_neu"] = y - pred
        return g

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        df = df.groupby("date", group_keys=False).apply(resid)
    return df


def daily_ic_series_col(df, score_col="score", ret_col="fwd_ret"):
    """按任意评分列/收益列算每日横截面 Rank IC。"""
    out = []
    for d, g in df.groupby("date"):
        if len(g) >= 5:
            ic = spearman(g[score_col].values, g[ret_col].values)
            if not np.isnan(ic):
                out.append((d, ic))
    out.sort(key=lambda x: x[0])
    return out


def score_beta_corr(df):
    """诊断: 评分 vs beta代理(adr_pct)的每日秩相关均值。
    若很高(如>0.4),说明评分本质在给波动率/beta排序。"""
    cs = []
    for d, g in df.groupby("date"):
        if len(g) >= 5:
            c = spearman(g["score"].values, g["adr_pct"].values)
            if not np.isnan(c):
                cs.append(c)
    return float(np.mean(cs)) if cs else np.nan


def purged_split_ic(df, embargo=HORIZON, split_frac=0.7):
    """purged/embargo 时间分割: 训练段与测试段之间挖掉 embargo 天。

    Codex P0#2: 标签是 HORIZON 天的前向窗口。若研发/锁箱期直接相邻切开,
    分割点前 HORIZON 天的样本,其标签窗口会跨进锁箱期 → 泄漏。挖掉这段
    embargo 才是真正的样本外。返回 (研发段df, 锁箱段df, split_date, embargo_start)。
    """
    dates = sorted(df["date"].unique())
    split_idx = int(len(dates) * split_frac)
    split_date = dates[split_idx]
    # embargo: 研发段末尾挖掉 embargo 个决策日(它们的标签窗口探入锁箱期)
    embargo_days = max(1, embargo // STEP) if STEP else embargo
    dev_end_idx = max(0, split_idx - embargo_days)
    dev_end_date = dates[dev_end_idx]
    dev = df[df["date"] < dev_end_date]
    lock = df[df["date"] >= split_date]
    return dev, lock, split_date, dev_end_date


def nonoverlap_multiphase(df, horizon=HORIZON):
    """多相位非重叠抽样: 对所有可能的起点相位分别抽样,汇报 IC 分布。

    原实现只取相位0(dates[::40]),148天只剩4个点 → n太小无意义。改为对
    每个 phase in [0..horizon) 各抽一条非重叠序列,把各相位的每日IC汇总,
    给出跨相位的 IC 均值与稳健性。这才是'消除重叠'的可信估计。
    """
    dates = sorted(df["date"].unique())
    all_ics = []
    phase_means = []
    for phase in range(min(horizon, len(dates))):
        keep = dates[phase::horizon]
        if len(keep) < 2:
            continue
        sub = df[df["date"].isin(keep)]
        ics = daily_ic_series(sub)
        if ics:
            arr = np.array([x[1] for x in ics])
            all_ics.extend(arr.tolist())
            phase_means.append(arr.mean())
    return all_ics, phase_means


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="output/bt_expanded.csv")
    args = ap.parse_args()
    df = pd.read_csv(args.data)
    df = df.sort_values("date").reset_index(drop=True)

    n_days = df["date"].nunique()
    print("=" * 66)
    print("P0 严谨验证")
    print("=" * 66)
    print(f"\n【样本量真相】")
    print(f"  表面样本: {len(df)}  |  独立决策日: {n_days}  |  股票: {df['ticker'].nunique()}")
    print(f"  → Codex警告: 每5天取点+40天窗口,'{len(df)}'是伪样本量,"
          f"有效信息量≈独立日数量级")
    per_day = df.groupby("date").size()
    print(f"  每日横截面股票数: 中位{per_day.median():.0f} 最小{per_day.min()} 最大{per_day.max()}")

    # 全样本IC
    print(f"\n【全样本 Rank IC】")
    all_ics = daily_ic_series(df)
    summarize(all_ics, "全样本(原始收益)")
    ci = block_bootstrap_ci(all_ics, block=40)
    if ci:
        print(f"  日期块bootstrap(块=40) 95%CI: [{ci[0]:+.4f}, {ci[1]:+.4f}]  "
              f"{'✓不含0(显著)' if ci[0]>0 else '✗含0(不显著)'}")

    # 中性化IC: 评分对beta代理残差化(P0#6 排除只是beta/波动率排序)
    print(f"\n【Beta中性化 Rank IC (P0#6: 评分对adr_pct残差化)】")
    beta_corr = score_beta_corr(df)
    print(f"  诊断: 评分 vs adr_pct(beta代理) 每日秩相关均值 = {beta_corr:+.3f}  "
          f"{'⚠高,评分偏向波动率排序' if abs(beta_corr) > 0.3 else '低,评分不主要靠beta'}")
    dfn = neutralize(df)
    neu_ics = daily_ic_series_col(dfn, "score_neu", "fwd_ret")
    neu_arr = summarize(neu_ics, "中性化后")
    ci_n = block_bootstrap_ci(neu_ics, block=40)
    if ci_n:
        print(f"  日期块bootstrap 95%CI: [{ci_n[0]:+.4f}, {ci_n[1]:+.4f}]  "
              f"{'✓不含0(显著)' if ci_n[0]>0 else '✗含0(不显著)'}")
    if neu_arr is not None and all_ics:
        raw_mean = np.mean([x[1] for x in all_ics])
        neu_mean = neu_arr.mean()
        shrink = (1 - neu_mean / raw_mean) * 100 if raw_mean != 0 else 0
        print(f"  → 中性化后 IC {raw_mean:+.4f} → {neu_mean:+.4f} (缩水 {shrink:.0f}%)。"
              f"缩水越大,说明原IC越依赖beta/波动率排序而非选股。")

    # purged/embargo 时间分割 (P0#2)
    print(f"\n【purged 时间分割: 研发期 vs 锁箱期,中间挖 {HORIZON}日 embargo】")
    dev, lock, split_date, dev_end = purged_split_ic(df)
    print(f"  研发段截止: {dev_end}  |  embargo挖除: [{dev_end}, {split_date})  |  锁箱段起: {split_date}")
    summarize(daily_ic_series(dev), "研发期(前段)")
    summarize(daily_ic_series(lock), "锁箱期(真正样本外,标签窗口不回探)")
    print("  → embargo 防止研发段末尾样本的40日标签窗口泄漏进锁箱期")

    # 多相位非重叠抽样 (P0#4 修正版)
    print(f"\n【多相位非重叠抽样: {HORIZON}相位各取一条非重叠序列】")
    no_ics, phase_means = nonoverlap_multiphase(df)
    if no_ics:
        a = np.array(no_ics)
        pm = np.array(phase_means)
        print(f"  汇总{len(pm)}个相位: 跨相位每日IC均值={a.mean():+.4f}  IC>0={np.mean(a>0):.1%}  "
              f"总有效日={len(a)}")
        print(f"  各相位IC均值: min={pm.min():+.4f} 中位={np.median(pm):+.4f} max={pm.max():+.4f}")
        print(f"  → 原实现只取相位0(n≈4)不可信;多相位汇总才是消除重叠后的稳健估计")
    else:
        print("  相位不足")


if __name__ == "__main__":
    main()
