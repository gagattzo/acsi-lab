# Stack proxy basket on Hyperliquid

Tracks the Stack **junior** score with a fixed-weight basket of existing xyz HIP-3 perps on Hyperliquid:
short `xyz:ORCL`, `xyz:IREN`, `xyz:NBIS`, long `xyz:XYZ100`. No new market, no oracle, no deployer stake.

## Verdict

- It is a **replica, not a forecast**. It moves with the equity layer (L1) of the junior score and is mostly blind
  to the CDS layer (L2).
- Out of sample it explains about a third of daily junior moves. Good enough for "tracking does not need to be
  perfect"; not good enough to settle anything against.
- The tracker (`python -m hl.tracker`) is dry-run by default. It has been tested offline, on mainnet read endpoints
  and with a throwaway key on testnet. **It has never placed a live mainnet order.**

## The fit

Code: `research/hl-proxy-basket/` (see its README). Summary numbers: `research/hl-proxy-basket/core4_results.json`.

- Method: non-negative least squares with fixed signs (short the borrowers, long the Nasdaq-100), daily change of
  the junior level on returns of session D-1 (the session that feeds L1 for as_of D). QQQ in the fit, `xyz:XYZ100`
  on Hyperliquid.
- Sample: 428 days, 2025-04-01 to 2026-11-26. Final coefficients (junior points per 100% move):
  ORCL -64.41, IREN -26.52, NBIS -15.36, QQQ 200.19.
- Walk-forward out of sample (expanding window, refit every 21 days, from 2025-10-01, n=302):
  R2 0.317, correlation 0.569, direction hit 70.4%, tracking error
  4.47 points a day against an index daily SD of 5.42. Block bootstrap 90% range for R2: 0.25 to 0.39.
- Before and after L2 started counting (July 2026): R2 0.317 and 0.317, so the fit did not
  degrade when CDS entered.
- Big moves (|change| >= 5 points, n=93): direction right 82%, but only
  37% of the size captured.
- CDS-driven days (L2 move larger than L1 move, |change| >= 3, n=24): 25% captured,
  9 of 24 with the wrong sign. Equity-driven days: 54% captured.
- Sizing: $32.63 of basket P&L per junior point per $10,000 gross. Basket beta to QQQ about 0.04.

### Caveats

- **Research backfill series.** The junior series used here was rebuilt locally with L2 forced to count from
  2026-06-30 (the live L2 gate was not met in the local build). It differs from published editions on some early
  November dates. Refit on published editions once there are enough of them.
- **CDS miss.** The basket cannot see a CDS-only widening. That is the main tracking gap.
- **Funding not modeled.** The fit ignores perp funding; the tracker only guards against high funding (hourly
  threshold). Shorts currently receive and the XYZ100 long pays, all small, but this can flip.
- **Lag.** The junior print for as_of D uses the US session of D-1, so the basket leads the index by about a day.
- **Yahoo vs HL basis.** The fit uses Yahoo closes; the perps track their oracles with a basis (checked in `fit3.py`).
- Coefficients are refit quarterly at most; never tuned to make a period look good.

## Files

| File | What |
|---|---|
| `hl/tracker.py` | The tracker: dry-run plan, `--live`, `--flatten`, `--status` (`python -m hl.tracker`) |
| `hl/basket_logic.py` | Pure sizing, rounding, delta and response-parsing logic (no network) |
| `hl/proxy_basket.py` | Read-only printer of the target basket (`python -m hl.proxy_basket 10000`) |
| `hl/config.example.yaml` | Every setting with its default |
| `hl/testnet_check.py` | Optional: one order to TESTNET from a throwaway key, expected to be rejected |
| `hl/deploy/` | systemd service and Monday timer |
| `tests/test_hl.py` | Unit tests; the signature test is skipped without `requirements-hl.txt` |
| `requirements-hl.txt` | `hyperliquid-python-sdk==0.24.1`, only for live trading and the testnet check |

## Setup


1. In the Hyperliquid app: More > API, create an API wallet (agent). It can trade for your account but cannot withdraw.
   Copy its private key once. Note your master account address (the wallet you log in with), not the agent's.
2. Collateral. Check your account mode (`--status` prints it):
   - Unified account: your USDC balance collateralizes xyz positions directly.
   - Standard (manual) mode: each dex has its own balance. Move USDC to the xyz dex yourself in the app.
     The agent key cannot do this transfer (it is a user-signed action).
3. Install:
   ```
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-hl.txt
   cp hl/config.example.yaml hl/config.yaml   # edit gross_usd etc.
   ```
4. Environment (never pass the key on the command line; it would land in shell history and `ps`):
   ```
   read -s HL_AGENT_KEY && export HL_AGENT_KEY
   export HL_ACCOUNT=0xYourMasterAddress
   ```
5. Dry-run (default, sends nothing; reads your positions if HL_ACCOUNT is set):
   ```
   .venv/bin/python -m hl.tracker --config hl/config.yaml
   ```
6. Live, inside the US cash session (Mon-Fri 09:30-16:00 ET):
   ```
   .venv/bin/python -m hl.tracker --config hl/config.yaml --live
   ```
7. Status and kill switch:
   ```
   .venv/bin/python -m hl.tracker --status
   .venv/bin/python -m hl.tracker --flatten          # shows the closes
   .venv/bin/python -m hl.tracker --flatten --live   # sends reduce-only closes for every leg, any hour
   ```

## What a live run does

0. Takes a file lock (a lock file in `$XDG_RUNTIME_DIR` or `/tmp`, keyed by network and account) so two runs never overlap. Checks `HL_ACCOUNT` is a 0x address
   (never echoes the value) and validates every config number (no negative gross or scale, slippage up to 5%).
1. Reads `perpDexs` and `metaAndAssetCtxs` for the xyz dex. Resolves each leg's asset id as
   `100000 + dex_index * 10000 + index_in_meta` and checks the SDK computes the same id. Never hardcode ids:
   on testnet xyz is dex 65 and NBIS sits at a different index.
2. Aborts if: the xyz dex or a leg is missing or delisted; a price is missing; mid is more than 2% from the oracle;
   gross, or projected gross after orders at worst price plus slippage, exceeds `max_total_notional_usd`; our side
   of funding costs more than `max_funding_cost_hourly` on any leg; or it is outside the US cash session (unless
   `--allow-off-hours`).
3. Signer check: the SDK does not sign `account_address`; HL picks the account from the signer. So before any order
   the script asks `userRole` that the key is an API wallet approved by exactly `HL_ACCOUNT`, and that `HL_ACCOUNT`
   is a plain user (vaults and sub-accounts are refused).
4. Open orders: if any basket leg has a resting order, a rebalance aborts; `--flatten` cancels them first.
5. Phase 1 sends reduce-only orders (shrinks and flip closes). Then it re-reads positions and prices, re-plans, and
   phase 2 sends opening orders only for legs that need no more closing. A partly filled flip close never gets an
   opening order on top of it. Leverage is set isolated (default 2x, capped at the asset max) before opening.
6. Every order response is parsed. A rejected order, a partial fill or a resting ALO order makes the run exit 3
   (`INCOMPLETE`), logged as `rebalance_incomplete`. Exit codes: 0 done, 2 aborted before trading, 3 traded but
   incomplete (check `--status`).
7. `--flatten` uses its own path: it ignores the gross cap, the funding check and the mid-vs-oracle check (warns
   instead), closes every leg it can with reduce-only IOC, closes dust below $10, and exits 3 if anything is left.
8. Logs every plan, leverage change, order, response and parsed result to `data/hl/hl_tracker.jsonl` (gitignored). The log writer
   refuses any line containing the agent key.

`order_style: alo` (post-only at mid) leaves resting orders that the next run will refuse to trade around. Use
`ioc` (the default) unless you will watch it.

## Hyperliquid quirks found

- Names: builder-dex coins are `xyz:ORCL`, not `ORCL`. The SDK needs `perp_dexs=['', 'xyz']` to load them.
- Asset ids on mainnet: XYZ100 110000, ORCL 110011, NBIS 110086, IREN 110109 (xyz is dex index 1).
- Signer: orders are not bound to `account_address` by the SDK; HL uses the signer's owner. The tracker checks `userRole`.
- Margin: ORCL, NBIS and IREN are `onlyIsolated` (`marginMode: noCross`), max 10x. XYZ100 allows cross, max 30x.
  The tracker uses isolated on every leg.
- Size steps: XYZ100 0.0001, ORCL 0.001, NBIS 0.01, IREN 0.1. Prices: 5 significant figures and at most
  6 - szDecimals decimals (XYZ100 trades in whole points).
- Funding is quoted per hour; xyz funding multiplier is 0.5 on all four legs.
- Hours (trade.xyz docs): single stocks have external pricing 24/5, Sunday 8:05 PM ET to Friday 8:00 PM ET;
  on weekends and US equity holidays the oracle follows the xyz order book (30-minute EMA, discovery bound +/-10%).
  XYZ100 has external pricing Sunday 6 PM to Friday 5 PM ET, with a daily 5 to 6 PM ET maintenance window,
  bound +/-3.5%. All trade 24/7 and can be liquidated off-hours. The timer runs Monday 15:30 UTC to stay in
  the cash session.

## Schedule

Clone the repo to `/opt/acsi-lab`, put `HL_AGENT_KEY=` and `HL_ACCOUNT=` in
`/etc/aistress-hl-tracker.env` (chmod 600), then:
```
sudo cp hl/deploy/aistress-hl-tracker.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now aistress-hl-tracker.timer
```
Cron alternative (server clock in UTC): `30 15 * * 1 cd /opt/acsi-lab && . /etc/aistress-hl-tracker.env && .venv/bin/python -m hl.tracker --config hl/config.yaml --live`

## Not verified

- The live path (`--live`) has not placed a real order. Signing and the HIP-3 asset id were checked on testnet with a
  throwaway key (server rejected it as an unknown user, naming the right signer).
- `cumFunding.sinceOpen` sign convention is read as positive = paid. Check against the app on the first live week.
- In unified account mode, per-dex balances in `clearinghouseState` are not meaningful (HL docs); positions are.
- `userRole` for an approved agent is read per the HL docs (`{"role":"agent","data":{"user":...}}`); not
  yet seen with a real agent.
- Whether HL accepts a reduce-only close under $10 (dust) is not verified.
- US holidays are not in the session check; the mid-vs-oracle guard catches most stale days, not all.
