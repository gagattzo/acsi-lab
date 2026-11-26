# Frozen legs, the real one-trade attack, and the minimum-prints rule (AGENT-BRIEF P1)

**Agent:** Claude (Opus 5.5), run by @gagattzo, from Marcello Bardus's AGENT-BRIEF P1 · **Status:** harness changed; `claude-min-prints` proposed

## Questions
1. Did painting the 24 Nov 2026 loan and GPU values across the year distort the baseline scorecard?
2. How far can one trade really move the index?
3. Does the brief's eligibility rule (5 prints on 3 of the last 5 sessions, one in the last 2) fix it without lagging
   real repricings?

## 1. Frozen legs
`methods/v0.1-live.yaml` held F3 (loans), A1 and A2 (GPU rents) at their 24 Nov 2026 values and applied them to every
day back to Oct 2025. The harness now counts a held value only from its `as_of` date. Before that date the day is scored
on the financing side alone (F1 and F2), and a change is scored only between days resting on the same inputs.

Result: the brief's own test ("overstated if the changes were already just the two CDS legs") partly holds. The
independence result does not move (R² 0.017 either way): with constant legs, every daily change was already a CDS
change. Two things were wrong, though:
- **The level history was not real.** A gap of +40 or so across 2025 used GPU rents nobody had observed then. There is
  now no gap before 24 Nov 2026, only the financing side, and the scorecard says so.
- **A constant F3 diluted every CDS move by a third,** so the one-trade figure looked smaller than it is (5.41 → 8.12 on
  the same measure, before the fix below).

## 2. The real one-trade attack
The old measure removed one trade at a time within a day. A day resting on a single trade scored 0, because there was
nothing left to compare. That is exactly the day an attacker would pick. The harness now asks what the input would show
without that trade: if the rest still sets a level, that level; if not, the level it would have carried.

| v0.1-live, full year | Old measure | Honest measure |
| --- | --- | --- |
| Most one trade moved it (points, financing side) | 8.12 | **21.24** |
| 95th percentile | 1.04 | **6.84** |
| Days a level is set by a single trade | n/a | 32% |

The typical case: a quiet CoreWeave day with one print. CoreWeave's anchors are 500 to 1,000 bp, so a single print 200
bp away from the carried level moves F1 by 40 points and the financing side by 20.

## 3. The minimum-prints rule
`methods/claude-min-prints.yaml`: on both CDS inputs, `window_sessions: 5, min_prints: 5, min_sessions_with_prints: 3,
max_days_since_print: 2, level: median_of_daily_medians`.

| | v0.1-live | claude-min-prints | First half | Second half |
| --- | --- | --- | --- | --- |
| Most one trade moved it | 21.24 | **8.24** | 6.06 (base 18.47) | 8.24 (base 21.24) |
| 95th percentile, one trade | 6.84 | **3.78** | 3.34 (base 6.84) | 3.78 (base 7.01) |
| Days on an input with under 3 trades | 77% | **35%** | 44% (base 82%) | 28% (base 72%) |
| Days a level is set by a single trade | 32% | **0%** | 0% | 0% |
| R² on the Nasdaq / all three factors | 0.017 / 0.038 | 0.003 / 0.012 | | |
| St. dev. of daily change (reported, not judged) | 4.41 | 2.39 | | |
| Scored days | 262 | 217 | | |

It wins on every judged test, in both halves. The cost is lag. Oracle repriced from about 330 to 365 bp between 20 and
24 Nov 2026:

| Oracle level (bp) | 20 Nov | 23 Nov | 24 Nov | 25 Nov |
| --- | --- | --- | --- | --- |
| v0.1-live | 330.4 | 334.1 | 364.5 | 373.1 |
| claude-min-prints | 316.1 | 323.9 | 332.3 | 349.3 |

By 25 Nov the rule is still 24 bp behind. That is more than a session. The brief's kill line needs that lag *and* no
clear cut in single-trade influence, and the cut is clear (21.2 → 8.2), so the rule is not refuted. The lag is a real
cost that a human has to weigh. Adoption waits for the 20-day forward test in any case.

**A variant that failed.** To answer the lag, `level_sessions: 2` (same guard, level from the last 2 sessions only) was
registered before it was scored, with this kill line: "it loses the single-trade gains". It did: most one trade 21.24,
the same as the baseline, because a level resting on one print in the last 2 sessions is a single-trade level again.
Oracle 349.3 / 368.8 bp on 24 / 25 Nov. Refuted, and not tuned further. The `level_sessions` feature stays in the
harness for anyone who wants to try a different guard.

## How to reproduce
```
python harness/dtcc.py --days 400
python harness/score.py methods/claude-min-prints.yaml --baseline methods/v0.1-live.yaml
python redteam/one_trade.py
```

**Checked: most lone prints were news, not noise.** On 67 CoreWeave days with a single print, the next print landed
within 50 bp on 53 (79%), with a median gap of 26.7 bp. So 21.2 points measures what one print *can* do, which is the
exposure an attacker would use, not how often the index was actually pushed around. It also names the cost of any
guard: it delays the 79% of lone prints that were real.

## What would prove it wrong
- The one-trade measure overstates the risk if lone prints are almost always confirmed (79% are within 50 bp today).
  Watch the other 21%: 14 days in the year where a lone CoreWeave print was not confirmed.
- claude-min-prints is the wrong default if in the forward test it lags a repricing visible in more than one market by
  more than a session and the single-trade cut does not hold.
