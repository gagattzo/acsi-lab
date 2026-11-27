"""L3 private loan marks: m1 same-facility markdown, m2 breadth, m3 added PIK, m4 non-accrual (SEC BDC Data Sets).
TODO (chunk 3): not implemented yet; returns status not_implemented, never a number."""


def compute(as_of, method, ctx):
    p = method['layers']['L3']
    return ({'layer': 'L3', 'name': p['name'], 'kind': p['kind'], 'as_of': as_of, 'status': 'not_implemented',
             'reason': 'L3 BDC marks not written yet (chunk 3)', 'available': None,
             'Z': None, 'level': None, 'published': None, 'band': None, '_series': []}, [])
