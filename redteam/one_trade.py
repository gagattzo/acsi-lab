"""Red team, finding #1: how far one trade can move the gap.

For every scored day, removes the single most influential CDS trade and reports how much the gap would change.

  python redteam/one_trade.py [methods/v0.1-live.yaml]
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'harness'))
import yaml
from score import load_trades, build, check_method, ROOT

method = check_method(yaml.safe_load(open(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'methods' / 'v0.1-live.yaml')))
trades, cal = load_trades(ROOT / 'data' / 'canonical' / 'cds_trades.csv')
s = build(method, trades, cal)
print(f"{'date':<11} {'one trade moves it':>19}  trades that day")
for x in sorted(s, key=lambda x: -x['influence'])[:10]:
    held = ', '.join(f"{k} {len(trades[k][x['date']])}" for k in ('CRWV', 'ORCL') if x['date'] in trades.get(k, {}))
    print(f"{x['date']:<11} {x['influence']:>18.2f}   {held}")
print(f"\n{len(s)} days scored. {sum(1 for x in s if x['thin']) / len(s):.0%} rest on an input with fewer than 3 trades or no trade that day.")
