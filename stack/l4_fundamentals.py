"""L4 fundamentals: f1 funding gap, f2 leverage to revenue, f3 leverage effect of interest burden, f4 8-K debt events
(SEC companyfacts and submissions, joined on the filed date). TODO (chunk 4): not implemented yet; returns status
not_implemented, never a number."""


def compute(as_of, method, ctx):
    p = method['layers']['L4']
    return ({'layer': 'L4', 'name': p['name'], 'kind': p['kind'], 'as_of': as_of, 'status': 'not_implemented',
             'reason': 'L4 SEC fundamentals not written yet (chunk 4)', 'available': None,
             'Z': None, 'level': None, 'published': None, 'band': None, '_series': []}, [])
