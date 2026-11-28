"""Fixed-universe AI cash-bond research, version research-v1.

Portal snapshots lack verified correction linkage. This is never a settlement feed.
See docs/bond-research-v1.md for eligibility, dates, default and publication rules.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pandas.tseries.holiday import AbstractHolidayCalendar, USFederalHolidayCalendar, GoodFriday

ROOT = Path(__file__).resolve().parent.parent
METHOD = 'bond-research-v1'
MIN_SIZE = 100_000
MIN_PRINTS = 3
MAX_CARRY = 5

def bond_sessions(start, end):
    """Verified dated SIFMA calendar; no extrapolation into unverified years."""
    if start > end: return []
    cfg=json.loads((ROOT/'config/bond_calendar.json').read_text())
    if start.isoformat()<cfg['valid_from'] or end.isoformat()>cfg['valid_through']:
        raise ValueError('Bond calendar outside verified coverage; update dated SIFMA calendar')
    holidays=set(cfg['closed_dates']); opened=set(cfg.get('open_overrides',[]))
    return [d.date().isoformat() for d in pd.date_range(start,end)
            if d.date().isoformat() in opened or (d.weekday()<5 and d.date().isoformat() not in holidays)]


def expected_session(now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo('America/New_York'))
    # Conservative end-of-day availability cutoff; holiday/early close days stay dated.
    end = now.date() if (now.hour, now.minute) >= (18, 0) else now.date() - timedelta(days=1)
    return bond_sessions(end-timedelta(days=14), end)[-1]


def atomic_json(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')
    os.replace(tmp, path)


def parse_treasury(raw_text):
    curve = {}
    tenors = {'1 Mo':1/12, '2 Mo':2/12, '3 Mo':.25, '4 Mo':1/3, '6 Mo':.5,
              '1 Yr':1, '2 Yr':2, '3 Yr':3, '5 Yr':5, '7 Yr':7, '10 Yr':10, '20 Yr':20, '30 Yr':30}
    for row in csv.DictReader(io.StringIO(raw_text)):
        try:
            day = datetime.strptime(row['Date'], '%m/%d/%Y').date().isoformat()
        except (ValueError, KeyError):
            continue
        rates = {}
        for key, tenor in tenors.items():
            try:
                value = float(row[key])
                if math.isfinite(value): rates[tenor] = value
            except (ValueError, TypeError, KeyError):
                pass
        if rates: curve[day] = rates
    return curve


def fetch_treasury_curve(cache_path=None, years=None):
    """Refresh requested years every run, archive originals, fall back transparently.

    Cached rows may supply older research dates but NEVER substitute another date.
    """
    root = Path(cache_path or 'private_data/treasury')
    if root.suffix: root = root.parent / 'treasury'
    root.mkdir(parents=True, exist_ok=True)
    rows, errors = {}, []
    for year in sorted(set(years or [datetime.now(timezone.utc).year])):
        current = root / f'{year}.csv'
        if current.exists(): rows.update(parse_treasury(current.read_text()))
        url = f'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv'
        failure = None
        for attempt in range(2):
            try:
                with urlopen(Request(url, headers={'User-Agent':'AISTRESS-Research/1.0'}), timeout=25) as response:
                    raw = response.read().decode('utf-8-sig')
                parsed = parse_treasury(raw)
                if not parsed or any(int(d[:4]) != year for d in parsed):
                    raise ValueError('Treasury response missing expected year')
                digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
                archive = root / f'{year}_{max(parsed)}_{digest}.csv'
                if not archive.exists(): archive.write_text(raw)
                current.write_text(raw); rows.update(parsed); failure = None; break
            except Exception as exc:
                failure = str(exc)
        if failure: errors.append({'year':year,'error':failure})
    return rows, errors


def interpolate_treasury_yield(curve_for_date, maturity_years):
    tenors = sorted(curve_for_date)
    if not tenors or not math.isfinite(maturity_years) or maturity_years <= 0:
        return None
    # No silent extrapolation beyond the observed Treasury maturity range.
    if maturity_years < tenors[0] or maturity_years > tenors[-1]: return None
    return float(np.interp(maturity_years, tenors, [curve_for_date[t] for t in tenors]))


def issuer_group(b):
    if b.get('issuer_group'): return b['issuer_group']
    name = b.get('issuer') or b.get('issuerName') or ''
    if name.upper() == 'COREWEAVE INC': return 'CoreWeave'
    if name.upper() in {'APLD COMPUTECO LLC','APLD COMPUTECO 2 LLC','APLD COMPUTECO 3 LLC'}:
        return 'Applied Digital'
    if not name: raise ValueError('Missing issuer group')
    return name


def eligible_trades(df, min_size=MIN_SIZE):
    required = {'cusip','tradeExecutionDate','reportedTradeVolume','lastSalePrice','lastSaleYield',
                'contraPartyTypeCode','reportingPartyTypeCode','tradeStatus'}
    missing = required - set(df.columns)
    if missing: raise ValueError('Required trade fields missing: ' + ','.join(sorted(missing)))
    out = df.copy()
    for key in ('reportedTradeVolume','lastSalePrice','lastSaleYield'):
        out[key] = pd.to_numeric(out[key], errors='coerce')
    mask = (out.contraPartyTypeCode.eq('C') & out.reportingPartyTypeCode.eq('D') &
            out.reportedTradeVolume.ge(min_size) & out.lastSalePrice.gt(0) &
            np.isfinite(out.lastSalePrice) & np.isfinite(out.lastSaleYield) &
            np.isfinite(out.reportedTradeVolume))
    # M/O/N are portal snapshot statuses, not lifecycle codes. No correction claim.
    mask &= out.tradeStatus.isin(['M','O','N'])
    for key in ['tradeModifierCode','tradeModifier2Code','specialPriceFlag']:
        if key in out: mask &= out[key].fillna('').astype(str).str.strip().eq('')
    if 'history_boundary_excluded' in out:
        mask &= ~out.history_boundary_excluded.eq(True)
    return out.loc[mask].copy()


def process_daily_bond_spreads(df_trades, universe, treasury_curve, max_carry_days=MAX_CARRY,
                               anchor_date=None, min_size=MIN_SIZE, min_prints=MIN_PRINTS,
                               default_events=None):
    if len({b['cusip'] for b in universe}) != len(universe): raise ValueError('Duplicate universe bond')
    if not universe: raise ValueError('Empty universe')
    anchor_date = anchor_date or expected_session()
    df = eligible_trades(df_trades, min_size)
    # Future source dates and non-session prints cannot move the reference.
    info = {b['cusip']:{**b,'issuer_group':issuer_group(b),'daily_inputs':[]} for b in universe}
    known = set(info); df = df[df.cusip.isin(known) & df.tradeExecutionDate.le(anchor_date)]
    start = min(df_trades.tradeExecutionDate.min(), anchor_date)
    days = bond_sessions(date.fromisoformat(start), date.fromisoformat(anchor_date))
    df = df[df.tradeExecutionDate.isin(days)]
    grouped = {}
    for (day, c), g in df.groupby(['tradeExecutionDate','cusip']):
        if len(g) < min_prints: continue
        years = (date.fromisoformat(info[c]['maturity'])-date.fromisoformat(day)).days/365.25
        rf = interpolate_treasury_yield(treasury_curve.get(day, {}), years)
        if rf is None: continue
        med_yield, med_price = float(g.lastSaleYield.median()), float(g.lastSalePrice.median())
        grouped[(day,c)] = {'last_date':day,'median_yield':med_yield,'median_price':med_price,
                           'g_spread_bp':(med_yield-rf)*100,'treasury_yield':rf,
                           'eligible_prints':len(g),'reported_volume_lower_bound':float(g.reportedTradeVolume.sum())}
    groups = sorted({issuer_group(b) for b in universe})
    weights = {g:1/len(groups) for g in groups}
    states, series = {}, []
    position = {d:i for i,d in enumerate(days)}
    for day in days:
        values, group_values, today, carried = {}, {}, 0, 0
        defaulted = any(e.get('default_date','9999-99-99') <= day for e in (default_events or []))
        for c in info:
            input_row={'date':day,'method_version':METHOD,'id':info[c].get('id',f'ACSI-BOND-{list(info).index(c)+1:02d}'), 'issuer_group':info[c]['issuer_group'],'status':'missing','source_date':None,'spread_bp':None, 'fixed_weight': weights[info[c]['issuer_group']]/sum(x['issuer_group']==info[c]['issuer_group'] for x in info.values())}
            info[c]['daily_inputs'].append(input_row)
            pt = grouped.get((day,c))
            if pt: states[c] = pt; today += 1
            st = states.get(c)
            if not st: continue
            elapsed = position[day]-position[st['last_date']]
            input_row.update(source_date=st['last_date'], stale_sessions=elapsed, median_price=st['median_price'], median_yield=st['median_yield'], treasury_yield=st['treasury_yield'], eligible_prints=st['eligible_prints'], status='stale' if elapsed>max_carry_days else 'carried' if elapsed else 'active')
            if elapsed > max_carry_days: continue
            if elapsed: carried += 1
            values[c] = st['g_spread_bp']
            input_row['spread_bp'] = st['g_spread_bp']
            group_values.setdefault(info[c]['issuer_group'],[]).append(st['g_spread_bp'])
        complete = len(values) == len(universe) and day in treasury_curve and not defaulted
        status = 'complete_research' if complete else ('default_rule_pending' if defaulted else
                 'missing_treasury' if day not in treasury_curve else 'incomplete_coverage')
        eq = sum(values.values())/len(universe) if complete else None
        cap = sum(weights[g]*sum(group_values[g])/len(group_values[g]) for g in groups) if complete else None
        series.append({'date':day,'method_version':METHOD,'status':status,'settlement_eligible':False,
                       'g_spread_equal_weighted_bp':round(eq,1) if eq is not None else None,
                       'g_spread_issuer_capped_bp':round(cap,1) if cap is not None else None,
                       'active_bonds_count':len(values),'active_issuers_count':len(group_values),
                       'basket_size':len(universe),'issuer_count':len(groups),'bonds_traded_today':today,
                       'carried_bonds_count':carried,'treasury_5y_yield':treasury_curve.get(day,{}).get(5)})
    return series, states, info


def export_public_safe_datasets(series, states, info, universe, output_dir, anchor_date, manifest=None):
    output_dir = Path(output_dir)
    public = []
    for n,b in enumerate(universe,1):
        c=b['cusip']; st=states.get(c,{}); last=st.get('last_date')
        age=len(bond_sessions(date.fromisoformat(last),date.fromisoformat(anchor_date)))-1 if last else None
        status='missing' if last is None else 'active' if age==0 else 'carried' if age<=MAX_CARRY else 'stale'
        public.append({'id':b.get('id',f'ACSI-BOND-{n:02d}'),'issuer':b.get('issuer') or b.get('issuerName'),
          'issuer_group':info[c]['issuer_group'],'tenant_group':b.get('category'),
          'description':b.get('description'),'coupon_rate':b['coupon'],'maturity_date':b['maturity'],
          'is_144a':b.get('is_144a') is True or b.get('is_144a')=='Y',
          'last_trade_date':last,'stale_sessions':age,'status':status,
          'latest_median_price':round(st['median_price'],4) if st else None,
          'latest_median_yield':round(st['median_yield'],6) if st else None,
          'latest_g_spread_bp':round(st['g_spread_bp'],1) if st else None,
          'eligible_prints':st.get('eligible_prints',0),'settlement_eligible':False})
    atomic_json(output_dir/'acsi_bond_spreads_daily.json',series)
    input_path=output_dir/'acsi_bond_inputs_daily.json'
    inputs=[r for x in info.values() for r in x.get('daily_inputs',[])]
    if input_path.exists() and inputs:
        old_inputs=json.loads(input_path.read_text()); retained={r['date'] for r in series if r.get('historical_input_retained')}
        inputs=[r for r in old_inputs if r.get('method_version')==METHOD and r['date'] in retained]+[r for r in inputs if r['date'] not in retained]
    atomic_json(input_path,inputs)
    atomic_json(output_dir/'acsi_bond_universe_latest.json',public)
    if manifest is not None: atomic_json(output_dir/'acsi_bond_manifest.json',manifest)


def preserve_prior_history(series, path, df):
    """Keep finalized same-method dates outside a new bounded snapshot's coverage.

    Archive every overwritten public version; never splice legacy-method levels.
    """
    path = Path(path)
    if not path.exists(): return series
    raw = path.read_bytes()
    prior = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()[:16]
    archive = path.parent / 'bond_revisions' / f'{path.stem}_{digest}.json'
    archive.parent.mkdir(parents=True,exist_ok=True)
    if not archive.exists(): archive.write_bytes(raw)
    window_start = max(df.groupby('cusip').tradeExecutionDate.min())
    current = {r['date']:r for r in series}
    for row in prior:
        if row.get('method_version') != METHOD or not row.get('publication_eligible'): continue
        if row['date'] < window_start:
            current[row['date']] = {**row,'historical_input_retained':True}
    return [current[d] for d in sorted(current)]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trades',default='private_data/trace_trades_clean.parquet')
    p.add_argument('--universe',default='private_data/bonds_universe.json')
    p.add_argument('--out',default='data')
    p.add_argument('--as-of',help='Explicit expected market session for reproducible research replay')
    p.add_argument('--treasury-file',help='Dated official Treasury CSV for offline reproduction')
    p.add_argument('--default-events',default='config/default_events.json')
    args=p.parse_args()
    df=pd.read_parquet(args.trades); universe=json.loads(Path(args.universe).read_text())
    previous_path=Path(args.out)/'acsi_bond_spreads_daily.json'
    previous={r['date']:r for r in json.loads(previous_path.read_text()) if r.get('method_version')==METHOD and r.get('publication_eligible')} if previous_path.exists() else {}
    if df.empty: raise SystemExit('No trade observations: preserve prior public artifacts')
    anchor=args.as_of or expected_session()
    if args.treasury_file:
        curve=parse_treasury(Path(args.treasury_file).read_text()); errors=[]
    else:
        years=range(int(df.tradeExecutionDate.min()[:4]),int(anchor[:4])+1)
        curve,errors=fetch_treasury_curve(Path(args.trades).parent/'treasury',years)
    if not Path(args.default_events).exists(): raise SystemExit('Default-event registry missing: preserve prior artifacts')
    defaults=json.loads(Path(args.default_events).read_text())['events']
    series,states,info=process_daily_bond_spreads(df,universe,curve,anchor_date=anchor,default_events=defaults)
    if not series: raise SystemExit('No session observations: preserve prior public artifacts')
    series=preserve_prior_history(series,Path(args.out)/'acsi_bond_spreads_daily.json',df)
    collection_path=Path(args.trades).parent/'trace_collection_manifest.json'
    collection=json.loads(collection_path.read_text()) if collection_path.exists() else {}
    source_date=str(df.loc[df.tradeExecutionDate.le(anchor), 'tradeExecutionDate'].max()) if df.tradeExecutionDate.le(anchor).any() else None
    complete=[r for r in series if r['status']=='complete_research']
    quality=series[-1]['status']
    if errors: quality='treasury_fetch_error'
    if not collection: quality='collection_provenance_unverified'
    elif collection.get('status') not in {'complete','success','ok','research_snapshot'}: quality='collection_error'
    if quality in {'treasury_fetch_error','collection_provenance_unverified','collection_error'}:
        # Failure cannot mint a new fixing from a mixture of cached and fresh inputs.
        for idx,row in enumerate(series):
            if row['date'] in previous:
                series[idx]=previous[row['date']]
            else:
                row.update(status=quality,g_spread_equal_weighted_bp=None,g_spread_issuer_capped_bp=None,publication_eligible=False)
        complete=[r for r in series if r['status']=='complete_research']
    for row in series:
        row['publication_eligible']=row['status']=='complete_research' and row['g_spread_issuer_capped_bp'] is not None
    now=datetime.now(timezone.utc).isoformat()
    manifest={'method_version':METHOD,'generated_at':now,'updated_at':now,
      'as_of':source_date,'source_market_date':source_date,'expected_session':anchor,'expected_market_date':anchor,
      'status':quality,'quality_status':quality,'settlement_eligible':False,'perp_eligible':False,'publication_eligible':quality=='complete_research',
      'latest_complete_date':complete[-1]['date'] if complete else None,
      'basket_size':len(universe),'issuer_count':len(set(issuer_group(b) for b in universe)),
      'active_bonds_count':series[-1]['active_bonds_count'],'active_issuers_count':series[-1]['active_issuers_count'],'carried_bonds_count':series[-1]['carried_bonds_count'],
      'minimum_customer_trade_size':MIN_SIZE,'minimum_eligible_prints':MIN_PRINTS,'max_carry_sessions':MAX_CARRY,
      'treasury_errors':errors,'source_fetched_at':collection.get('fetched_at') or collection.get('completed_at'),
      'warnings':['Research only: portal snapshots do not establish cancellation/correction lineage or a complete historical tape.',
                  'Median reported yields minus same-date interpolated Treasury par yields; reported yield-to-call/maturity conventions are not independently verified.',
                  'Equal issuer weights do not remove common tenant or guarantor exposure. No rating is assigned to the basket.',
                  'No default/recovery fixing rule is calibrated: a declared default freezes basket publication pending methodology review.',
                  'Partial historical baskets have no published level; changes require the same fixed universe and method.']}
    export_public_safe_datasets(series,states,info,universe,Path(args.out),anchor,manifest)
    print(json.dumps({'as_of':source_date,'expected_session':anchor,'status':quality,'latest':series[-1]}))
    if quality!='complete_research': raise SystemExit(2)

if __name__=='__main__': main()
