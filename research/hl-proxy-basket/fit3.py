import numpy as np, pandas as pd, json
exec(open('fit2.py').read().split("print('== alignment")[0])
# --- HL vs Yahoo basis check
hl=pd.read_pickle('hl_hourly.pkl'); px=pd.read_csv('yahoo_closes.csv',index_col=0,parse_dates=True)
mp={'xyz:ORCL':'ORCL','xyz:CRWV':'CRWV','xyz:NBIS':'NBIS','xyz:IREN':'IREN','xyz:XYZ100':'QQQ','xyz:SMH':'SMH','xyz:META':'META'}
basis={}
for c,t in mp.items():
    s=hl[c]; s=s[(s.index.hour==20)&(s.index.minute==59)]; s.index=s.index.tz_convert(None).normalize()
    yv=px[t].dropna(); j=pd.concat([s.rename('hl'),yv.rename('y')],axis=1,join='inner').pct_change(fill_method=None).dropna()
    basis[c]=dict(n=len(j),corr=round(j.corr().iloc[0,1],3),te_bp=round((j.hl-j.y).std()*1e4))
print('basis',basis)
# --- walk-forward expanding, refit every 21 obs, constrained7 and ridge, lag alignment
cols=SHORT+LONG
Xa,ya,ia=frame(XL,cols,'2025-04-01','2026-11-26')
Xr,yr,ir=frame(XL,ALL,'2025-04-01','2026-11-26')
sign=np.array([-1 if c in SHORT else 1 for c in cols])
pc=np.full(len(ya),np.nan); coefs=[]
for t0 in range(126,len(ya),21):
    x=nnls(Xa[:t0]*sign,ya[:t0])*sign; coefs.append((str(ia[t0].date()),dict(zip(cols,np.round(x,1)))))
    pc[t0:t0+21]=Xa[t0:t0+21]@x
m=~np.isnan(pc); wf=stats(ya[m],pc[m]); print('walkforward constrained7',wf, 'from',ia[m][0].date())
for d,c in coefs: print('  ',d,c)
# --- final fit on all data
xfin=nnls(Xa*sign,ya)*sign; print('final coef',dict(zip(cols,np.round(xfin,2))),'in-sample',stats(ya,Xa@xfin))
# --- slices of OOS walk-forward predictions
J=pd.DataFrame(json.load(open('junior_research.json'))).set_index('date'); J.index=pd.to_datetime(J.index)
df=pd.DataFrame({'y':ya,'p':pc},index=ia).dropna()
df['dL1']=0.6*J['L1z'].diff().reindex(df.index); df['dL2']=0.4*J['L2z'].diff().reindex(df.index)
l2era=df.index>=pd.Timestamp('2026-07-01')
print('pre-L2 era',stats(df.y[~l2era].values,df.p[~l2era].values))
print('L2 era',stats(df.y[l2era].values,df.p[l2era].values))
big=df.y.abs()>=5; print('big |d|>=5',stats(df.y[big].values,df.p[big].values), 'capture ratio', round((df.p[big]*np.sign(df.y[big])).sum()/df.y[big].abs().sum(),3))
e=df[l2era].dropna(); cds=(e.dL2.abs()>e.dL1.abs())&(e.y.abs()>=3)
print('CDS-driven days n',cds.sum(), stats(e.y[cds].values,e.p[cds].values),'capture', round((e.p[cds]*np.sign(e.y[cds])).sum()/e.y[cds].abs().sum(),3))
print(e[cds][['y','p','dL1','dL2']].round(2).to_string())
top=df.reindex(df.y.abs().sort_values(ascending=False).index[:10]); print(top.round(2).to_string())
df.to_csv('wf_preds.csv')
json.dump({'basis':basis,'wf':wf,'final':dict(zip(cols,[float(v) for v in xfin])),'coefs':coefs},open('fit7_summary.json','w'),default=str)
