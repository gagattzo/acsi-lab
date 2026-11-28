# Does the index snap to the Nasdaq under stress? Mostly no, and the weekly number depends on the start day

**Agent:** Claude (Opus 5.5), run by @gagattzo · follows issue #2 and `redteam/nasdaq_redteam.py` · **Status:** finding; no method change

## Question
`redteam/nasdaq_redteam.py` reports a rolling 10-day R² peak of 0.797 (beta −3.50 points per 1% Nasdaq move) and a
weekly R² that can reach about 0.22. A proposed pitch read this as "uncorrelated on calm days, tightly coupled on stress
days". Do the numbers support that?

## Method
`redteam/nasdaq_null.py`, on the same data, for `v0.1-live` and `v0.2-live` (the live method):
1. **Weekly start day.** Non-overlapping 5-session changes can start on any of five sessions. All five are reported.
2. **Rolling peaks against chance.** The index's daily changes are rotated against the Nasdaq's daily returns (every
   circular shift of at least 15 sessions, 232 in all). A rotation keeps each series' own patterns but breaks any link
   between them. The p-value is the share of rotations that match or beat the real figure.
3. **Stress days.** R² and the average index move on the Nasdaq's worst 10% and 5% days, with the same rotation test for
   the average move. Plus the one-day lag.

## Results (261 paired sessions, 27 Oct 2025 to 25 Nov 2026)

| | v0.1-live | v0.2-live |
| --- | --- | --- |
| Daily R² | 0.011 | 0.050 |
| Weekly R², by start session | 0.046 · 0.219 · 0.131 · 0.084 · 0.005 | 0.097 · 0.250 · 0.285 · 0.086 · 0.022 |
| Rolling 10-day peak R² (chance median, p) | 0.797 (0.632, p 0.09) | 0.842 (0.642, p 0.03) |
| Rolling 10-day mean R² (chance median, p) | 0.229 (0.107, p < 0.01) | 0.223 (0.110, p < 0.01) |
| Rolling 20-day peak R² (chance median, p) | 0.565 (0.312, p 0.01) | 0.565 (0.318, p < 0.01) |
| Worst 10% Nasdaq days (26): R² · average index move (chance, p) | 0.001 · +0.39 (+0.18, p 0.40) | 0.007 · +1.39 (+0.20, p 0.07) |
| Worst 5% Nasdaq days (13): R² · average index move (chance, p) | 0.007 · +0.35 (+0.21, p 0.46) | 0.048 · +1.87 (+0.34, p 0.08) |
| Middle 80% of days: average index move | +0.18 | +0.25 |
| One-day lag, Nasdaq(t−1) → index(t), R² | 0.031 | 0.015 |

## What this says
- **No tight coupling on stress days.** On the Nasdaq's worst days the day-to-day fit stays loose (R² 0.001 to 0.048).
  "Correlation snaps shut under stress" is not supported.
- **The weekly R² is not one number.** Depending on which session the weeks start, it runs from 0.005 to 0.219 (v0.1) or
  0.022 to 0.285 (v0.2). Quote the range or none of it.
- **The single 10-day peak is weak evidence** (p 0.09 on v0.1, 0.03 on v0.2, and it is the best of 252 windows).
- **Short episodes of co-movement are real.** The average 10-day and 20-day R² sit about twice the chance level, and the
  20-day peaks beat chance. They come and go; they are not tied to selloff days.
- **A hint, not a result, on v0.2:** with no ceiling, the index rises more on the Nasdaq's worst days (+1.4 to +1.9
  points against +0.2 to +0.3 by chance), at p ≈ 0.07–0.08. Worth re-testing as data accumulates; not worth claiming.
- **Part of the low daily R² is slowness, not independence.** 77% of days are thin and 32% rest on one trade (see the
  scorecard), and yesterday's Nasdaq explains a little more than today's on v0.1.

## What to say publicly
"Broad markets explain under 8% of daily moves; weekly estimates are unstable (0.5% to 29% depending on the start day);
there are short spells of co-movement, but no evidence that it locks to the Nasdaq on selloff days." Do not pitch
independence or "tail beta" on these numbers.

## Update, 28 Nov 2026: rerun on the tape with contract checks
`research/findings/2026-11-28-cds-contract-checks.md` removed records that are not prices (packages, novations, odd
lots, post-priced trades) and cancellations filed later. Same script, 259 paired sessions (28 Oct 2025 to 25 Nov 2026):

| | v0.1-live | v0.2-live |
| --- | --- | --- |
| Daily R² | 0.023 | 0.083 |
| Weekly R², by start session | 0.071 · 0.173 · 0.173 · 0.037 · 0.053 | 0.239 · 0.170 · 0.221 · 0.079 · 0.088 |
| Rolling 10-day peak R² (chance median, p) | 0.726 (0.653, p 0.24) | 0.726 (0.653, p 0.23) |
| Rolling 20-day peak R² (chance median, p) | 0.570 (0.311, p 0.02) | 0.570 (0.316, p 0.01) |
| Worst 10% Nasdaq days (25): R² · average index move (chance, p) | 0.000 · +0.06 (+0.14, p 0.55) | 0.004 · +1.46 (+0.15, p 0.09) |
| Worst 5% Nasdaq days (12): R² · average index move (chance, p) | 0.089 · −0.38 (+0.12, p 0.71) | 0.107 · +1.01 (+0.28, p 0.24) |
| One-day lag R² | 0.021 | 0.006 |

The conclusions hold: no lock-in on selloff days, a weekly R² that depends on the start day, short spells of
co-movement. The levels moved: cleaner data shows more daily co-movement (v0.2 daily R² 0.05 → 0.08) and the 10-day
peak is now chance. Say publicly: "Broad markets explain about 5% to 11% of daily moves; weekly estimates run 4% to 24%
depending on the start day; no evidence it locks to the Nasdaq on selloff days."

## How to reproduce
```
python harness/dtcc.py --days 400
python redteam/nasdaq_redteam.py methods/v0.1-live.yaml
python redteam/nasdaq_null.py methods/v0.1-live.yaml methods/v0.2-live.yaml
```

## What would prove it wrong
- A stress-day R² above 0.3 on the worst 10% of Nasdaq days, or a selloff-day average move with p < 0.01 under the
  rotation test, on the same or later data.
- A weekly R² that stays within a few points across all five start sessions.
- A null that respects the data better than rotation (for example a block bootstrap) and puts the rolling averages back
  inside chance.
