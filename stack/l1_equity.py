"""L1 equity: E1 AI borrowers, E2 AI-HPC miners, E3 exposed-lender spread (METHODOLOGIES Candidate A), residualized on
a rolling 126-session fit. TODO (chunk 2): not implemented yet; returns status not_implemented, never a number."""


def compute(as_of, method, ctx):
    p = method['layers']['L1']
    return ({'layer': 'L1', 'name': p['name'], 'kind': p['kind'], 'as_of': as_of, 'status': 'not_implemented',
             'reason': 'L1 equity loader and residual fit not written yet (chunk 2)', 'available': None,
             'Z': None, 'level': None, 'published': None, 'band': None, '_series': []}, [])
