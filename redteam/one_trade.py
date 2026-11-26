"""Red team, finding #1: how far one trade can move the gap.

For every scored day, removes the single most influential CDS trade and reports how much the gap would change.
Lists the worst days and how many trades the input rested on.

  python redteam/one_trade.py methods/v0.1-live.yaml
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'harness'))
import yaml
from score import load_cds, build, ROOT

method = yaml.safe_load(open(sys.argv[1] if len(sys.argv) > 1 else ROOT / 'methods' / 'v0.1-live.yaml'))
cds = load_cds(ROOT / 'data' / 'cds_daily.csv')
s = build(method, cds)
worst = sorted(s, key=lambda x: -x['influence'])[:10]
print(f"{'date':<11} {'gap':>7} {'one trade moves it':>19}  inputs that day (trades)")
for x in worst:
    held = ', '.join(f"{k} {cds[k][x['date']]['trades']}" for k in ('CRWV', 'ORCL') if x['date'] in cds.get(k, {}))
    print(f"{x['date']:<11} {x['G']:>+7.1f} {x['influence']:>18.2f}   {held}")
thin = sum(1 for x in s if x['thin']) / len(s)
print(f"\n{len(s)} days scored. {thin:.0%} rest on an input with fewer than 3 trades or no trade that day.")
