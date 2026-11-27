import numpy as np, pandas as pd, json
exec(open('fit2.py').read().split("print('== alignment")[0])
cols=['ORCL','IREN','NBIS','QQQ']; shorts=['ORCL','IREN','NBIS']; sign=np.array([-1 if c in shorts else 1 for c in cols])
Xa,ya,ia=frame(XL,cols,'2025-04-01','2026-11-26')
pc=np.full(len(ya),np.nan)
for t0 in range(126,len(ya),21):
    x=nnls(Xa[:t0]*sign,ya[:t0])*sign; pc[t0:t0+21]=Xa[t0:t0+21]@x
xf=nnls(Xa*sign,ya)*sign
J=pd.DataFrame(json.load(open('junior_research.json'))).set_index('date'); J.index=pd.to_datetime(J.index)
df=pd.DataFrame({'y':ya,'p':pc},index=ia).dropna()
df['dL1']=0.6*J['L1z'].diff().reindex(df.index); df['dL2']=0.4*J['L2z'].diff().reindex(df.index)
res={'n_total':len(ya),'final_coef':dict(zip(cols,[round(float(v),2) for v in xf])),'insample':stats(ya,Xa@xf),'wf':stats(df.y.values,df.p.values),'wf_from':str(df.index[0].date())}
l2=df.index>=pd.Timestamp('2026-07-01'); res['pre']=stats(df.y[~l2].values,df.p[~l2].values); res['l2']=stats(df.y[l2].values,df.p[l2].values)
big=df.y.abs()>=5; res['big']=stats(df.y[big].values,df.p[big].values); res['big_capture']=round(float((df.p[big]*np.sign(df.y[big])).sum()/df.y[big].abs().sum()),3)
e=df[l2]; cds=(e.dL2.abs()>e.dL1.abs())&(e.y.abs()>=3); res['cds_n']=int(cds.sum()); res['cds']=stats(e.y[cds].values,e.p[cds].values); res['cds_capture']=round(float((e.p[cds]*np.sign(e.y[cds])).sum()/e.y[cds].abs().sum()),3)
eq=(e.dL1.abs()>=e.dL2.abs())&(e.y.abs()>=3); res['eq_n']=int(eq.sum()); res['eq_capture']=round(float((e.p[eq]*np.sign(e.y[eq])).sum()/e.y[eq].abs().sum()),3)
# opposite sign on CDS days
res['cds_wrongsign']=int((np.sign(e.p[cds])!=np.sign(e.y[cds])).sum())
g=sum(abs(v) for v in xf); res['usd_per_point_10k']=round(10000/g,2); res['w10k']={c:round(float(v)/g*10000) for c,v in zip(cols,xf)}
print(json.dumps(res,indent=1,default=float))
json.dump(res,open('core4_results.json','w'),default=float)
df.round(3).to_csv('core4_wf.csv')
# research series, weekly sampled last 2y + monthly older
s=J['level']; print('research range',s.index.min().date(),s.index.max().date(),len(s))
w=s.loc['2024-11-26':].resample('W-FRI').last(); print(len(w)); json.dump([[str(i.date()),round(v,1)] for i,v in w.items()],open('weekly.json','w'))
print('daily change abs sd last2y',round(s.loc['2024-11-26':].diff().std(),2),'max',round(s.loc['2024-11-26':].diff().abs().max(),2))
