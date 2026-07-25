# Directional Signal Labels and Bearish Risk Memory

## Goal

Make every reversal/setup label state its direction and model family, and keep a detected bearish turn risk visible for 5–10 trading sessions instead of recomputing the displayed state from zero each day.

## Naming

The UI uses these names consistently in Chinese and English:

| Current | Chinese | English | Model source |
| --- | --- | --- | --- |
| 反转候选 2/3 | 向上结构反转候选 2/3 | Bullish structural reversal candidate 2/3 | 三条件价格结构规则模型 |
| 早期反转观察 | 向上早期反转观察 | Early bullish reversal watch | 四条件规则评分模型 |
| 下跌转折风险 | 向下转折风险 | Bearish turn risk | 12项市场/板块/个股规则评分模型 |
| 紧密平台 / VCP | 向上突破准备形态 | Bullish breakout setup | VCP数学形态规则 |

Internal field names remain stable where they are public API or saved-model contracts. This is a presentation and metadata migration, not a destructive schema rename.

## Risk Memory Model

The existing 12-rule daily score remains the raw score and the source evidence remains unchanged. A separate causal state layer consumes only raw scores available on or before each observation date.

- Memory half-life: 5 trading sessions.
- Maximum memory age: 10 trading sessions.
- Recurrence: `state[t] = max(raw[t], state[t-1] * 0.5 ** (1 / 5))`.
- When the remembered peak reaches age 10, it expires and the state returns to the current raw score.
- Missing raw scores produce an unavailable state rather than fabricated values.
- An active state has a score of at least 20.
- Status is `new` when an inactive state becomes active, `persistent` when current evidence renews an already-active state, `fading` when remembered risk exceeds current raw risk, and `inactive` below 20.

The API preserves `downside_risk.score` as the raw score for compatibility and adds:

- `raw_score`
- `state_score`
- `state`
- `memory_age_sessions`
- `memory_half_life_sessions`
- `memory_window_sessions`
- `model_key`

Market UI headlines and table cells display `state_score` first. The accompanying text shows the status, current raw score, and memory age. Scores are rule indices, not probabilities.

## Historical and Group Data

`build_group_score_frame` keeps `downside_risk_score` unchanged and adds state columns. This preserves current calibration and saved-model inputs while allowing historical state analysis.

Selected-group risk aggregates constituent state scores for the displayed state and retains a separate aggregate raw score. The same point-in-time cutoff used by the current market context applies to all memory calculations.

## Verification

- Unit-test decay, renewal, expiration, missing values, and future-row invariance.
- Regression-test a MU-like sequence: raw risk `34 → 15 → 5` must remain visibly active on the third session.
- Verify API fields and group aggregation.
- Verify all Chinese/English labels and explanatory model-source text.
- Run the complete Python and JavaScript test suites.
