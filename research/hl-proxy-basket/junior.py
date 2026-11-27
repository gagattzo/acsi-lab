import sys, pickle, json
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from stack import build
d=pickle.load(open('layers.pkl','rb')); method=d['method']; res=d['res']
l2=[p for p in res['L2']['_series'] if p.get('Z') is not None]
print('L2 Z from',l2[0]['session'],'n',len(l2))
l1=[p for p in res['L1']['_series'] if p.get('Z') is not None]
print('L1 Z from',l1[0]['session'],'n',len(l1))
for p in res['L2']['_series']:
    p['scored']= p.get('Z') is not None
comp=method['composites']['junior']
dates,comps,parts=build.composite_series('junior',comp,res,method,'2026-11-26')
rows=[]
for D,c,pp in zip(dates,comps,parts):
    if c.get('level') is None: continue
    rows.append({'date':D,'level':round(c['level'],3),'Z':round(c['Z'],4),'status':c['status'],
      'w':{k:round(v,3) for k,v in c['weights_used'].items()},
      'L1z':pp['L1']['z'],'L2z':pp['L2']['z'],'L1st':pp['L1']['status'],'L2st':pp['L2']['status']})
json.dump(rows,open('junior_research.json','w'))
print(len(rows),rows[0]['date'],rows[-1]['date'])
for r in rows[-8:]: print(r['date'],r['level'],r['w'],r['L1st'],r['L2st'])
