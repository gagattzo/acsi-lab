import unittest
from datetime import datetime, timezone
import pandas as pd
from harness.bond_spreads import (bond_sessions,expected_session,eligible_trades,
 process_daily_bond_spreads,issuer_group,interpolate_treasury_yield)


def bond(c, issuer):
 return {'cusip':c,'issuer':issuer,'maturity':'2031-01-01','coupon':6,'is_144a':True}
def trade(c, day, y=8, price=95, **kw):
 return dict(cusip=c,tradeExecutionDate=day,lastSaleYield=y,lastSalePrice=price,
 reportedTradeVolume=100000,contraPartyTypeCode='C',reportingPartyTypeCode='D',tradeStatus='M',**kw)
def rows(c,day,y=8): return [trade(c,day,y+i*.01) for i in range(3)]
def curves(days): return {d:{1:4.,5:4.,10:4.} for d in days}

class ResearchTests(unittest.TestCase):
 def test_holidays_include_bond_only_and_good_friday(self):
  from datetime import date
  self.assertNotIn('2026-11-11',bond_sessions(date(2026,11,10),date(2026,11,12)))
  self.assertIn('2026-04-03',bond_sessions(date(2026,4,2),date(2026,4,6)))
  self.assertNotIn('2026-10-12',bond_sessions(date(2026,10,9),date(2026,10,13)))
 def test_weekend_expected_date(self):
  self.assertEqual(expected_session(datetime(2026,11,28,12,tzinfo=timezone.utc)),'2026-11-27')
 def test_today_before_close_not_expected(self):
  self.assertEqual(expected_session(datetime(2026,11,30,16,tzinfo=timezone.utc)),'2026-11-27')
 def test_filters_customer_size_modifiers_boundary(self):
  base=trade('a','2026-11-27'); rs=[base]
  for k,v in [('reportedTradeVolume',99999),('contraPartyTypeCode','D'),('tradeModifier2Code','P'),('history_boundary_excluded',True),('tradeStatus','X')]:
   rs.append({**base,k:v})
  self.assertEqual(len(eligible_trades(pd.DataFrame(rs))),1)
 def test_extreme_distress_yield_not_discarded(self):
  self.assertEqual(len(eligible_trades(pd.DataFrame([trade('a','2026-11-27',70)]))),1)
 def test_partial_basket_no_survivor_average(self):
  df=pd.DataFrame(rows('a','2026-11-27'))
  s,_,_=process_daily_bond_spreads(df,[bond('a','A'),bond('b','B')],curves(['2026-11-27']),anchor_date='2026-11-27')
  self.assertIsNone(s[-1]['g_spread_issuer_capped_bp'])
 def test_fixed_groups_and_mean_not_median(self):
  u=[bond('a','A'),bond('b','A'),bond('c','C')]
  df=pd.DataFrame(rows('a','2026-11-27',8)+rows('b','2026-11-27',10)+rows('c','2026-11-27',20))
  s,_,_=process_daily_bond_spreads(df,u,curves(['2026-11-27']),anchor_date='2026-11-27')
  self.assertAlmostEqual(s[-1]['g_spread_issuer_capped_bp'],1051)
 def test_single_print_does_not_fix_level(self):
  s,_,_=process_daily_bond_spreads(pd.DataFrame([trade('a','2026-11-27')]),[bond('a','A')],curves(['2026-11-27']),anchor_date='2026-11-27')
  self.assertIsNone(s[-1]['g_spread_equal_weighted_bp'])
 def test_uses_median_not_volume_weighted(self):
  rs=rows('a','2026-11-27',8);rs.append({**trade('a','2026-11-27',30),'reportedTradeVolume':1000000})
  s,_,_=process_daily_bond_spreads(pd.DataFrame(rs),[bond('a','A')],curves(['2026-11-27']),anchor_date='2026-11-27')
  self.assertEqual(s[-1]['g_spread_equal_weighted_bp'],401.5)
 def test_missing_exact_curve_no_fallback(self):
  s,_,_=process_daily_bond_spreads(pd.DataFrame(rows('a','2026-11-27')),[bond('a','A')],curves(['2026-11-25']),anchor_date='2026-11-27')
  self.assertEqual(s[-1]['status'],'missing_treasury');self.assertIsNone(s[-1]['g_spread_equal_weighted_bp'])
 def test_stale_calendar_counts_no_trade_days(self):
  from datetime import date
  days=bond_sessions(date(2026,11,16),date(2026,11,27))
  s,_,_=process_daily_bond_spreads(pd.DataFrame(rows('a','2026-11-16')),[bond('a','A')],curves(days),anchor_date='2026-11-27')
  self.assertEqual(s[-1]['active_bonds_count'],0)
 def test_default_freezes_not_drop(self):
  s,_,_=process_daily_bond_spreads(pd.DataFrame(rows('a','2026-11-27')),[bond('a','A')],curves(['2026-11-27']),anchor_date='2026-11-27',default_events=[{'default_date':'2026-11-27','issuer_group':'A'}])
  self.assertEqual(s[-1]['status'],'default_rule_pending');self.assertIsNone(s[-1]['g_spread_equal_weighted_bp'])
 def test_no_treasury_extrapolation(self):
  self.assertIsNone(interpolate_treasury_yield({1:4,5:4},7))
 def test_separate_tract_spvs(self):
  self.assertNotEqual(issuer_group(bond('a','SV RNO PPTY OWNER 1 LLC')),issuer_group(bond('b','PR RNO PPTY OWNER 1 LLC')))

 def test_calendar_unverified_year_fails_closed(self):
  from datetime import date
  with self.assertRaises(ValueError): bond_sessions(date(2030,1,1),date(2030,1,2))
 def test_treasury_parsing_year_rollover(self):
  from harness.bond_spreads import parse_treasury
  c=parse_treasury('Date,1 Yr,5 Yr,10 Yr\n01/04/2027,4,4.2,4.4\n12/31/2026,4.1,4.3,4.5\n')
  self.assertIn('2027-01-04',c);self.assertIn('2026-12-31',c)
 def test_retained_history_rejects_legacy_and_unverified(self):
  import tempfile,json
  from pathlib import Path
  from harness.bond_spreads import preserve_prior_history,METHOD
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.json';p.write_text(json.dumps([{'date':'2026-11-01','method_version':'legacy','publication_eligible':True},{'date':'2026-11-02','method_version':METHOD,'publication_eligible':False},{'date':'2026-11-03','method_version':METHOD,'publication_eligible':True}]))
   r=preserve_prior_history([],p,pd.DataFrame(rows('a','2026-11-27')))
   self.assertEqual([x['date'] for x in r],['2026-11-03'])

if __name__=='__main__': unittest.main()
