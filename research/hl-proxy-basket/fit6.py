import numpy as np, pandas as pd, json
exec(open('fit2.py').read().split("print('== alignment")[0])
def wfp(cols,shorts,first=126,step=21):
    Xa,ya,ia=frame(XL,['ORCL','IREN','NBIS','QQQ'],'2025-04-01','2026-11-26')
    idx=[['ORCL','IREN','NBIS','QQQ'].index(c) for c in cols]; Xa=Xa[:,idx]
    sign=np.array([-1 if c in shorts else 1 for c in cols]); pc=np.full(len(ya),np.nan)
    for t0 in range(first,len(ya),step):
        x=nnls(Xa[:t0]*sign,ya[:t0])*sign; pc[t0:t0+step]=Xa[t0:t0+step]@x
    m=~np.isnan(pc); return ya[m],pc[m]
for name,cols,sh in [('ORCL alone',['ORCL'],['ORCL']),('QQQ alone (long)',['QQQ'],[]),('ORCL+QQQ',['ORCL','QQQ'],['ORCL']),('core4',['ORCL','IREN','NBIS','QQQ'],['ORCL','IREN','NBIS'])]:
    yt,yp=wfp(cols,sh); print(name,stats(yt,yp))
yt,yp=wfp(['ORCL','IREN','NBIS','QQQ'],['ORCL','IREN','NBIS'])
rng=np.random.default_rng(0); B=10; n=len(yt); r2=[];cs=[]
for _ in range(2000):
    starts=rng.integers(0,n-B,size=n//B+1); ii=np.concatenate([np.arange(s,s+B) for s in starts])[:n]
    s=stats(yt[ii],yp[ii]); r2.append(s['R2']); cs.append(s['corr'])
print('R2 90% CI',np.round(np.percentile(r2,[5,95]),3),'corr CI',np.round(np.percentile(cs,[5,95]),3))
up=yt>=5; dn=yt<=-5
for lab,m in [('up>=5',up),('down<=-5',dn)]: print(lab,m.sum(),'capture',round(float((yp[m]*np.sign(yt[m])).sum()/np.abs(yt[m]).sum()),3),'hit',round(float(np.mean(np.sign(yp[m])==np.sign(yt[m]))),3))
