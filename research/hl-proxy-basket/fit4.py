import numpy as np, pandas as pd, json
exec(open('fit2.py').read().split("print('== alignment")[0])
def wf(cols,shorts,start,end,first=126,step=21):
    Xa,ya,ia=frame(XL,cols,start,end); sign=np.array([-1 if c in shorts else 1 for c in cols])
    pc=np.full(len(ya),np.nan)
    for t0 in range(first,len(ya),step):
        x=nnls(Xa[:t0]*sign,ya[:t0])*sign; pc[t0:t0+step]=Xa[t0:t0+step]@x
    m=~np.isnan(pc); return stats(ya[m],pc[m]), str(ia[m][0].date()), x
for name,cols in [('core3 ORCL IREN QQQ',['ORCL','IREN','QQQ']),('core4 +NBIS',['ORCL','IREN','NBIS','QQQ']),('six',['ORCL','CRWV','NBIS','IREN','QQQ','SMH'])]:
    s,f,x=wf(cols,['ORCL','IREN','NBIS','CRWV'],'2025-04-01','2026-11-26'); print(name,s,f,np.round(x,1))
s,f,x=wf(['ORCL','IREN','QQQ'],['ORCL','IREN'],'2021-12-01','2026-11-26',first=250); print('core3 long history',s,'from',f,np.round(x,1))
s,f,x=wf(['ORCL','QQQ'],['ORCL'],'2019-02-01','2026-11-26',first=250); print('ORCL+QQQ since 2019',s,'from',f,np.round(x,1))
# basket beta
c={'ORCL':-60.56,'CRWV':-8.67,'NBIS':-12.26,'IREN':-25.29,'QQQ':181.39,'SMH':13.67}
R=XL[list(c)].loc['2025-04-01':].dropna(); b=R@pd.Series(c)/sum(abs(v) for v in c.values())
q=R['QQQ']; beta=np.cov(b,q)[0,1]/q.var(); print('basket beta to QQQ',round(beta,3),'daily vol %',round(b.std()*100,2),'ann vol %',round(b.std()*np.sqrt(252)*100,1))
