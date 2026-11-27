import json, numpy as np, pandas as pd
J=pd.DataFrame(json.load(open('junior_research.json'))).set_index('date'); J.index=pd.to_datetime(J.index)
px=pd.read_csv('yahoo_closes.csv',index_col=0,parse_dates=True)
px=px.rename(columns={'BTC-USD':'BTC'})
stocks=[c for c in px.columns if c!='BTC']
ret=px[stocks].dropna(how='all').pct_change(fill_method=None)
btc=px['BTC'].pct_change(fill_method=None)
ret['BTC']=btc.reindex(ret.index)
# target
J['d']=J['level'].diff()
# L1 session for D is previous trading day of ret index
idx=ret.index
def prev_session(D):
    i=idx.searchsorted(D)-1
    return idx[i] if i>=0 else None
rows=[]
for D,r in J.iterrows():
    s=prev_session(D)
    if s is None or pd.isna(r['d']): continue
    rows.append((D,s))
A=pd.DataFrame(rows,columns=['D','s']).set_index('D')
X_lag=ret.reindex(A['s']).set_axis(A.index)        # session feeding L1 (D-1)
X_same=ret.reindex(A.index)                          # session D (same day as CDS session)
y=J['d'].reindex(A.index)
dl2=J['L2z'].reindex(A.index)
data={'y':y,'Xlag':X_lag,'Xsame':X_same,'L2z':J['L2z'],'L1z':J['L1z']}
pd.to_pickle(data,'fitdata.pkl')
print(len(y), y.index.min(), y.index.max())
