import numpy as np, pandas as pd
d=pd.read_pickle('fitdata.pkl'); y=d['y']; XL=d['Xlag']; XS=d['Xsame']
def nnls(A,b,iters=5000):
    # Lawson-Hanson active set
    m,n=A.shape; P=[]; x=np.zeros(n); w=A.T@(b-A@x)
    for _ in range(iters):
        R=[j for j in range(n) if j not in P]
        if not R or max(w[R])<=1e-12: break
        j=R[int(np.argmax(w[R]))]; P.append(j)
        while True:
            z=np.zeros(n); z[P]=np.linalg.lstsq(A[:,P],b,rcond=None)[0]
            if min(z[P])>0: x=z; break
            neg=[k for k in P if z[k]<=0]
            alpha=min(x[k]/(x[k]-z[k]) for k in neg)
            x=x+alpha*(z-x); P=[k for k in P if x[k]>1e-12]
        w=A.T@(b-A@x)
    return x
def ridge(X,yv,lam):
    n=X.shape[1]; return np.linalg.solve(X.T@X+lam*np.eye(n),X.T@yv)
def stats(yt,yp):
    e=yt-yp; r2=1-np.sum(e**2)/np.sum((yt-yt.mean())**2)
    c=np.corrcoef(yt,yp)[0,1]; nz=yt!=0
    hit=np.mean(np.sign(yt[nz])==np.sign(yp[nz]))
    return dict(n=len(yt),R2=round(r2,3),corr=round(c,3),TE=round(np.std(e),2),sd_y=round(np.std(yt),2),hit=round(hit,3))
ALL=['ORCL','CRWV','NBIS','IREN','CIFR','META','NVDA','MSFT','BX','QQQ','SMH','TLT','BTC']
SHORT=['ORCL','CRWV','NBIS','IREN','META']; LONG=['QQQ','SMH']
def frame(X,cols,start,end):
    m=(X.index>=start)&(X.index<=end)
    Z=X.loc[m,cols]; yy=y[m]; ok=Z.notna().all(1)&yy.notna()
    return Z[ok].values, yy[ok].values, Z[ok].index
def run(X,label,start='2025-04-01',split='2026-04-01',end='2026-11-26'):
    out={}
    for name,cols,kind in [('ridge_all',ALL,'ridge'),('constrained7',SHORT+LONG,'nnls'),('short5_only',SHORT,'nnls')]:
        Xtr,ytr,_=frame(X,cols,start,pd.Timestamp(split)-pd.Timedelta(days=1)); Xte,yte,ite=frame(X,cols,split,end)
        if kind=='ridge':
            mu=Xtr.std(0); best=None
            # choose lam by simple holdout inside train (last 25%)
            k=int(len(ytr)*.75)
            for lam in [0.1,1,3,10,30,100,300]:
                b=ridge(Xtr[:k]/mu,ytr[:k],lam); s=stats(ytr[k:],(Xtr[k:]/mu)@b)['R2']
                if best is None or s>best[0]: best=(s,lam)
            b=ridge(Xtr/mu,ytr,best[1])/mu; coef=b
        else:
            sign=np.array([-1 if c in SHORT else 1 for c in cols])
            x=nnls(Xtr*sign,ytr); coef=x*sign
        out[name]=dict(cols=cols,coef=dict(zip(cols,np.round(coef,2))),train=stats(ytr,Xtr@coef),test=stats(yte,Xte@coef))
        print(label,name,'train',out[name]['train'],'\n   test',out[name]['test'],'\n   coef',out[name]['coef'])
    return out
print('== alignment: returns of session D-1 (feeds L1)'); rl=run(XL,'lag')
print('== alignment: returns of session D'); rs=run(XS,'same')
X2=XL.add(XS,fill_value=np.nan)
print('== alignment: 2-day return D-1 and D'); r2=run(X2,'two')
