import sys, json, pickle
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from stack import build, engine
as_of=sys.argv[1] if len(sys.argv)>1 else '2026-11-26'
method,h,res,inp=build.compute_layers(as_of,layers=['L1','L2'],offline=True)
out={}
for k in ['L1','L2']:
    s=res[k].get('_series',[])
    out[k]=s
    print(k,len(s), s[0] if s else None, s[-1] if s else None)
pickle.dump({'method':method,'res':{k:res[k] for k in ['L1','L2']}},open('layers.pkl','wb'))
