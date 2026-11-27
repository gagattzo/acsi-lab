"""L1 equity, residualized (METHODOLOGIES Candidate A legs E1-E3, rates and beta in section 2).

Legs (tickers in stack/stack-v0.yaml, layers.L1):
  E1 AI borrowers         CRWV, ORCL, NBIS
  E2 AI-HPC miners        IREN, CIFR, WULF, CORZ, APLD
  E3 exposed lenders      equal-weight daily log return of the configured BDCs minus the BDC benchmark (BIZD), as one
                          series. The BDC list is a placeholder until the exposure map's top 5 by AI exposure / NAV
                          replaces it.

Per name (each E1 and E2 ticker, and the E3 spread as one series), on each session t of the equity calendar (the
trading days of the first equity factor, QQQ):
  return     daily log return of the adjusted close, only between consecutive sessions (a gap or a membership
             boundary gives no return, so a stale price never leaks across it)
  residual   out of sample: OLS with intercept fitted on the name's valid returns among the previous fit_sessions (126)
             sessions, t itself excluded, then applied to day t. Factors: QQQ and SMH log returns, HYG minus IEF log
             return (or the change in HY OAS, configurable), the change in VIX (FRED VIXCLS or Yahoo ^VIX), the changes
             in DGS2 and DGS10. Miners can add a lagged BTC return (see below). Pure Python normal equations solved by
             Gaussian elimination with partial pivoting.
  s          minus the sum of the last sum_sessions (5) daily residuals: a negative equity residual is stress, so the
             sign is flipped and higher always means more stress
  z          robust z of s against the name's own previous s values (z_window 252, z_min_history needed, else
             insufficient_history)
The raw series is the same with the return in place of the residual. It is published next to the residual one (Z_raw,
raw contributions) and is not scored.

Layer: a name's base weight is its leg weight divided by the number of live names in the leg. A ticker listed in the
method's `groups` joins that borrower group; any other ticker is its own group; the E3 spread is one group (e3_group).
Group z = base-weighted mean of its names' z. Layer Z = sum of capped group weights x group z (20% cap with pro-rata
redistribution, engine.cap_weights). The layer is withheld when less than min_live_share of the leg weight is live. The
band is replayed over every residual session with junior hysteresis (2 consecutive fresh closes) and the 2-group rule.

Availability (no lookahead): an equity close is available on its own day. A FRED observation dated t is treated as
available on the next weekday (US holidays are not modelled, so this can be one day early around a holiday). A FRED
blank ('.', a bond-market holiday) means the market was closed: the change is 0 and flagged. The residual for t is
produced only once all of its factor observations are available, so an as-of D build usually ends at the previous
session for L1 (`session` is the last residual date, `available` the date it became computable). Adjusted closes are
used: a later dividend or split rescales earlier prices by a constant, which leaves earlier log returns unchanged.

BTC: Yahoo's BTC-USD daily bar dated d closes at 00:00 UTC on d+1, after the US close of d, so a same-day BTC return is
a lookahead. factors.btc = lagged uses, for session t, the close of the bar dated t-1 (00:00 UTC on t, before the US
close) and its change since the previous session. TODO: a BTC print at 21:00 UTC (the US close) needs another source.

Sources: prices from "Yahoo Finance via yfinance, research use, not republishable" (yfinance is an optional dependency,
imported lazily; see requirements-stack.txt), cached as data/raw/prices/<ticker>.csv with sha256 recorded; FRED from the
public fredgraph.csv URL, cached as data/raw/fred/<id>.csv. Neither is committed.

Windows, weights, floors and membership dates are proposed examples, not calibrated; a human freezes them before any
backfill is evidence.
"""
import bisect, csv, datetime as dt, io, math, pathlib, re

from . import engine
from .config import cuts as config_cuts
from .fetch import atomic_write, file_record, get as fetch_get

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_PRICES = 'Yahoo Finance via yfinance, research use, not republishable'
SOURCE_FRED = 'FRED (St. Louis Fed), public fredgraph.csv'
FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={}'
LEGS = ('E1', 'E2', 'E3')
BTC = 'BTC-USD'
VIX_YAHOO = '^VIX'
VIX_FRED = 'VIXCLS'
HY_OAS = 'BAMLH0A0HYM2'
HYG, IEF = 'HYG', 'IEF'
FRED_ID = re.compile(r'^[A-Z0-9]{2,30}$')
E3_PLACEHOLDER = ('placeholder list, to be replaced by the top 5 BDCs by AI exposure / NAV from the exposure map '
                  '(research/bdc)')
TODO = [
    'BTC control for E2: Yahoo BTC-USD closes at 00:00 UTC, so only a one-day lag is lookahead-free; a 21:00 UTC print '
    'needs another source (factors.btc: none by default)',
    'E3 lender list is a placeholder until the exposure map ranks the top 5 BDCs by AI exposure / NAV',
    'Membership dates are hand-entered from the prior backtest and must be verified against filings before freezing',
    'FRED availability uses the next weekday; US holidays are not modelled',
    'Yahoo Finance has no republication right: an official series needs a licensed or exchange end-of-day feed',
]


# --- dates -----------------------------------------------------------------------------------------------------------

def _iso(s):
    return dt.date.fromisoformat(s).isoformat()


def next_weekday(s):
    """The next weekday after date s (the FRED availability rule)."""
    d = dt.date.fromisoformat(s) + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.isoformat()


def weekday_on_or_before(s):
    d = dt.date.fromisoformat(s)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def parse_windows(specs):
    """Membership windows: ['2021-12-14:', ':2025-07-06'] -> [(from or None, to or None)], both ends inclusive."""
    out = []
    for s in specs:
        if not isinstance(s, str) or s.count(':') != 1:
            raise ValueError(f'membership window {s!r} must be "from:to" (either side may be blank)')
        a, b = (x.strip() or None for x in s.split(':'))
        a, b = (_iso(a) if a else None), (_iso(b) if b else None)
        if a and b and a > b:
            raise ValueError(f'membership window {s!r} ends before it starts')
        out.append((a, b))
    return out


def is_member(d, windows):
    if windows is None:
        return True
    return any((a is None or d >= a) and (b is None or d <= b) for a, b in windows)


# --- raw files -------------------------------------------------------------------------------------------------------

def parse_prices(b):
    """{date: adjusted close} from a cached price CSV (header date,adj_close). Raises on anything else."""
    rows = list(csv.reader(io.StringIO(b.decode('utf-8'))))
    if not rows or rows[0] != ['date', 'adj_close']:
        raise ValueError('price file header must be date,adj_close')
    out = {}
    for r in rows[1:]:
        if not r:
            continue
        d, v = r
        x = float(v)
        if not math.isfinite(x) or x <= 0:
            raise ValueError(f'bad price {v!r} on {d}')
        out[_iso(d)] = x
    if not out:
        raise ValueError('price file has no rows')
    return out


def prices_csv(series):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['date', 'adj_close'])
    for d in sorted(series):
        w.writerow([d, repr(float(series[d]))])
    return buf.getvalue().encode('utf-8')


def parse_fred(b):
    """{date: value or None} from a fredgraph.csv payload (header observation_date or DATE, then the series id).
    A blank or '.' value (no observation, e.g. a bond-market holiday) is None. Raises on anything else."""
    rows = list(csv.reader(io.StringIO(b.decode('utf-8-sig'))))
    if not rows or len(rows[0]) != 2 or rows[0][0] not in ('observation_date', 'DATE'):
        raise ValueError('not a fredgraph.csv payload')
    out = {}
    for r in rows[1:]:
        if not r:
            continue
        d, v = r
        v = v.strip()
        out[_iso(d)] = None if v in ('', '.') else float(v)
    if not any(v is not None for v in out.values()):
        raise ValueError('FRED file has no observations')
    return out


def _valid_prices(b):
    parse_prices(b)
    return True


def _valid_fred(b):
    parse_fred(b)
    return True


def _price_name(t):
    return re.sub(r'[^A-Za-z0-9.-]', '_', t) + '.csv'


def yf_download(tickers, start):
    """{ticker: {date: adjusted close}} from yfinance (optional dependency, imported here only)."""
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError('L1 prices need the optional yfinance package: .venv/bin/pip install -r requirements-stack.txt '
                           '(or run with --offline on a populated data/raw/prices/)') from e
    df = yf.download(list(tickers), start=start, auto_adjust=True, actions=False, progress=False, group_by='ticker',
                     threads=False)
    out = {}
    multi = getattr(df.columns, 'nlevels', 1) > 1
    for t in tickers:
        try:
            col = df[t]['Close'] if multi else df['Close']
        except KeyError:
            continue
        s = {idx.strftime('%Y-%m-%d'): float(v) for idx, v in col.dropna().items() if float(v) > 0}
        if s:
            out[t] = s
    return out


def load_prices(tickers, as_of, root, offline=False, start='2018-01-01', downloader=None):
    """Cached adjusted closes. A ticker is downloaded when it is not cached or its cache ends before the last weekday
    on or before as_of; only valid payloads are written (atomically). Returns (prices with date <= as_of, inputs,
    notes)."""
    pdir = pathlib.Path(root) / 'data' / 'raw' / 'prices'
    cache, status, notes = {}, {}, []
    for t in tickers:
        p = pdir / _price_name(t)
        if p.is_file():
            try:
                cache[t] = parse_prices(p.read_bytes())
                status[t] = 'cached'
            except (ValueError, UnicodeDecodeError) as e:
                notes.append(f'{t}: cached price file rejected ({e}), ignored')
    target = weekday_on_or_before(as_of)
    need = [t for t in tickers if t not in cache or max(cache[t]) < target]
    if need and not offline:
        try:
            got = (downloader or yf_download)(need, start)
        except Exception as e:  # noqa: BLE001 - a failed download writes nothing; the cache (if any) is used
            notes.append(f'price download failed: {type(e).__name__}: {e}')
            got = {}
        for t in need:
            s = got.get(t)
            if s and (t not in cache or max(s) >= max(cache[t])):
                b = prices_csv(s)
                parse_prices(b)
                atomic_write(pdir / _price_name(t), b)
                cache[t] = parse_prices(b)
                status[t] = 'downloaded'
            elif t in cache:
                status[t] = 'stale_cache'
            else:
                status[t] = 'failed'
    elif need:
        for t in need:
            status[t] = 'stale_cache' if t in cache else 'offline_missing'
    inputs = []
    for t in tickers:
        if t in cache:
            rec = file_record(pdir / _price_name(t), root)
            rec.update(source=SOURCE_PRICES, ticker=t)
            inputs.append(rec)
    stale = [t for t in tickers if status.get(t) == 'stale_cache']
    if stale:
        notes.append(f'prices not refreshed, older cache used for {", ".join(stale)}')
    missing = [t for t in tickers if t not in cache]
    if missing:
        notes.append(f'no prices for {", ".join(missing)} ({", ".join(sorted({status.get(t, "missing") for t in missing}))})')
    prices = {t: {d: v for d, v in s.items() if d <= as_of} for t, s in cache.items()}
    return prices, inputs, notes


def load_fred(ids, as_of, root, offline=False, opener=None):
    """FRED series via the public CSV, cached under data/raw/fred/. Refreshed when the cache ends before the weekday
    before as_of (the last observation that can be available on as_of). Returns ({id: {date: value}}, inputs, notes)."""
    raw = pathlib.Path(root) / 'data' / 'raw'
    out, inputs, notes = {}, [], []
    want = weekday_on_or_before((dt.date.fromisoformat(as_of) - dt.timedelta(days=1)).isoformat())
    for sid in ids:
        path = raw / 'fred' / f'{sid}.csv'
        refresh = False
        if path.is_file():
            try:
                refresh = max(parse_fred(path.read_bytes())) < want
            except (ValueError, UnicodeDecodeError):
                refresh = True
        r = fetch_get(FRED_URL.format(sid), 'fred', f'{sid}.csv', validate=_valid_fred, offline=offline,
                      refresh=refresh and not offline, root=raw, opener=opener, min_interval=0.5)
        if not r['ok']:
            notes.append(f'FRED {sid}: {r["status"]} ({r.get("error")})')
            continue
        if r['status'] == 'stale_cache':
            notes.append(f'FRED {sid}: not refreshed, older cache used ({r.get("error")})')
        out[sid] = parse_fred(r.data)
        rec = file_record(path, root)
        rec.update(source=SOURCE_FRED, series=sid, url=r['url'])
        inputs.append(rec)
    return out, inputs, notes


# --- factor columns --------------------------------------------------------------------------------------------------

def ret_col(px, sessions):
    """Log return between consecutive sessions; None when either close is missing."""
    out = [None]
    for i in range(1, len(sessions)):
        a, b = px.get(sessions[i - 1]), px.get(sessions[i])
        out.append(math.log(b / a) if a and b else None)
    return out


def change_col(px, sessions):
    out = [None]
    for i in range(1, len(sessions)):
        a, b = px.get(sessions[i - 1]), px.get(sessions[i])
        out.append(b - a if a is not None and b is not None else None)
    return out


def fred_change_col(obs, sessions):
    """[(change, available, flag)] per session for a FRED series. The change at t is the value dated t minus the
    latest numeric value dated before t. A blank at t (or no row at t but a later row exists) means the market was
    closed: change 0, flag 'closed'. No row at or after t: not published yet (None). Available on the next weekday
    after the row that tells us (t, or the first later row)."""
    dates = sorted(obs)
    num = [d for d in dates if obs[d] is not None]
    out = []
    for t in sessions:
        k = bisect.bisect_left(num, t)
        prev = obs[num[k - 1]] if k > 0 else None
        if t in obs and obs[t] is not None:
            out.append((None if prev is None else obs[t] - prev, next_weekday(t), None))
            continue
        j = bisect.bisect_left(dates, t)
        if j >= len(dates):
            out.append((None, None, 'not published'))
            continue
        tell = dates[j]  # t itself (a blank row) or the first later row
        out.append((None if prev is None else 0.0, next_weekday(tell), 'closed'))
    return out


def btc_lagged_col(btc, sessions):
    """Lookahead-free BTC return for session t: close of the bar dated t-1 (00:00 UTC on t, before the US close)
    against the same for the previous session."""
    def at(s):
        return btc.get((dt.date.fromisoformat(s) - dt.timedelta(days=1)).isoformat())
    out = [None]
    for i in range(1, len(sessions)):
        a, b = at(sessions[i - 1]), at(sessions[i])
        out.append(math.log(b / a) if a and b else None)
    return out


def factor_spec(fc):
    """[(name, kind, args)] for the common factors, in order."""
    spec = [(t, 'ret', (t,)) for t in fc['equity']]
    if fc['credit'] == 'hyg_ief':
        spec.append(('HYG-IEF', 'spread', (HYG, IEF)))
    else:
        spec.append((f'd{HY_OAS}', 'fred', (HY_OAS,)))
    if fc['vix'] == 'fred':
        spec.append((f'd{VIX_FRED}', 'fred', (VIX_FRED,)))
    else:
        spec.append(('dVIX', 'change', (VIX_YAHOO,)))
    spec += [(f'd{r}', 'fred', (r,)) for r in fc['rates']]
    return spec


def factor_rows(sessions, prices, fred, fc, as_of):
    """Per session: {'x': [factor values] or None, 'available': date or None, 'flags': [...], 'missing': reason}.
    A row is usable only when every factor has a value and is available on or before as_of."""
    spec = factor_spec(fc)
    cols = []
    for name, kind, args in spec:
        if kind == 'ret':
            cols.append([(v, s, None) for v, s in zip(ret_col(prices.get(args[0], {}), sessions), sessions)])
        elif kind == 'spread':
            a, b = ret_col(prices.get(args[0], {}), sessions), ret_col(prices.get(args[1], {}), sessions)
            cols.append([(None if x is None or y is None else x - y, s, None) for x, y, s in zip(a, b, sessions)])
        elif kind == 'change':
            cols.append([(v, s, None) for v, s in zip(change_col(prices.get(args[0], {}), sessions), sessions)])
        else:
            cols.append(fred_change_col(fred.get(args[0], {}), sessions))
    rows = []
    for i, s in enumerate(sessions):
        x, avail, flags, miss = [], s, [], []
        for (name, _, _), col in zip(spec, cols):
            v, a, f = col[i]
            if f:
                flags.append(f'{name} {f}')
            if v is None:
                miss.append(name)
            else:
                avail = max(avail, a)
            x.append(v)
        if miss:
            rows.append({'x': None, 'available': None, 'flags': flags, 'missing': f'factor missing: {", ".join(miss)}'})
        elif avail > as_of:
            rows.append({'x': None, 'available': avail, 'flags': flags,
                         'missing': f'factors not yet available (available {avail})'})
        else:
            rows.append({'x': x, 'available': avail, 'flags': flags, 'missing': None})
    return [n for n, _, _ in spec], rows


# --- OLS -------------------------------------------------------------------------------------------------------------

def solve(A, b):
    """Solve A x = b by Gaussian elimination with partial pivoting. None when A is singular."""
    n = len(b)
    M = [list(map(float, A[i])) + [float(b[i])] for i in range(n)]
    scale = max((abs(v) for row in A for v in row), default=0.0)
    if scale == 0:
        return None
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) <= 1e-13 * scale:
            return None
        M[c], M[p] = M[p], M[c]
        piv = M[c][c]
        for r in range(c + 1, n):
            f = M[r][c] / piv
            if f:
                Mr, Mc = M[r], M[c]
                for k in range(c, n + 1):
                    Mr[k] -= f * Mc[k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (M[r][n] - sum(M[r][k] * x[k] for k in range(r + 1, n))) / M[r][r]
    return x


def ols(X, y):
    """OLS with intercept: coefficients [a, b1, ..] of y = a + b . x. X: list of rows. None if singular."""
    k = len(X[0]) + 1
    A = [[0.0] * k for _ in range(k)]
    v = [0.0] * k
    for row, yy in zip(X, y):
        r = [1.0] + list(row)
        for p in range(k):
            v[p] += r[p] * yy
            for q in range(k):
                A[p][q] += r[p] * r[q]
    return solve(A, v)


def rolling_residuals(y, X, window=126, min_obs=120, recompute_every=63):
    """Out-of-sample residuals. For each i, OLS with intercept on the valid rows among i-window .. i-1 (i itself is
    never in its own fit), then residual y[i] - prediction. Rolling normal-equation sums, rebuilt from scratch every
    `recompute_every` steps to stop rounding drift. Returns one dict per i: status ok (resid, beta, n_fit), missing,
    no_fit (too few rows) or singular."""
    n = len(y)
    valid = [y[i] is not None and X[i] is not None for i in range(n)]
    k = None
    for i in range(n):
        if valid[i]:
            k = len(X[i]) + 1
            break
    if k is None:
        return [{'status': 'missing'} for _ in range(n)]
    A = [[0.0] * k for _ in range(k)]
    v = [0.0] * k
    cnt = 0

    def add(j, sgn):
        r = [1.0] + X[j]
        yy = y[j]
        for p in range(k):
            rp = sgn * r[p]
            v[p] += rp * yy
            Ap = A[p]
            for q in range(k):
                Ap[q] += rp * r[q]

    out = []
    for i in range(n):
        if i % recompute_every == 0:
            A = [[0.0] * k for _ in range(k)]
            v = [0.0] * k
            cnt = 0
            for j in range(max(0, i - window), i):
                if valid[j]:
                    add(j, 1.0)
                    cnt += 1
        else:
            if valid[i - 1]:
                add(i - 1, 1.0)
                cnt += 1
            j = i - 1 - window
            if j >= 0 and valid[j]:
                add(j, -1.0)
                cnt -= 1
        if not valid[i]:
            out.append({'status': 'missing'})
            continue
        if cnt < min_obs:
            out.append({'status': 'no_fit', 'n_fit': cnt})
            continue
        beta = solve(A, v)
        if beta is None:
            out.append({'status': 'singular', 'n_fit': cnt})
            continue
        pred = beta[0] + sum(b * x for b, x in zip(beta[1:], X[i]))
        out.append({'status': 'ok', 'resid': y[i] - pred, 'beta': beta, 'n_fit': cnt})
    return out


# --- names -----------------------------------------------------------------------------------------------------------

def member_prices(px, windows):
    return {d: v for d, v in px.items() if is_member(d, windows)}


def name_returns(p, prices, sessions, membership):
    """[{id, leg, y, constituents}] for every E1 and E2 ticker and the E3 spread."""
    out = []
    for leg in ('E1', 'E2'):
        for t in p[leg.lower()]:
            px = member_prices(prices.get(t, {}), membership.get(t))
            out.append({'id': t, 'leg': leg, 'y': ret_col(px, sessions), 'has_prices': t in prices})
    bench = ret_col(member_prices(prices.get(p['e3_benchmark'], {}), membership.get(p['e3_benchmark'])), sessions)
    lend = {t: ret_col(member_prices(prices.get(t, {}), membership.get(t)), sessions) for t in p['e3_lenders']}
    y, cons = [], []
    for i in range(len(sessions)):
        rs = [lend[t][i] for t in p['e3_lenders'] if lend[t][i] is not None]
        cons.append(len(rs))
        y.append(sum(rs) / len(rs) - bench[i] if rs and bench[i] is not None else None)
    out.append({'id': 'E3', 'leg': 'E3', 'y': y, 'constituents': cons, 'has_prices': any(t in prices for t in p['e3_lenders'])})
    return out


def score_name(y, res, p):
    """s (flipped 5-session residual sum) and its robust z, and the same on raw returns. Lists aligned to sessions."""
    S = p['sum_sessions']
    n = len(y)
    s, s_raw, z, z_raw, zst = [None] * n, [None] * n, [None] * n, [None] * n, [None] * n
    zinfo = [None] * n
    hist, hist_raw = [], []
    for i in range(n):
        if i >= S - 1:
            if all(res[j]['status'] == 'ok' for j in range(i - S + 1, i + 1)):
                s[i] = -sum(res[j]['resid'] for j in range(i - S + 1, i + 1))
            if all(y[j] is not None for j in range(i - S + 1, i + 1)):
                s_raw[i] = -sum(y[j] for j in range(i - S + 1, i + 1))
        if s_raw[i] is not None:
            r = engine.robust_z(s_raw[i], hist_raw, p['z_window'], p['z_min_history'], p['z_floor'])
            z_raw[i] = r['z']
            hist_raw.append(s_raw[i])
        if s[i] is not None:
            r = engine.robust_z(s[i], hist, p['z_window'], p['z_min_history'], p['z_floor'])
            z[i], zst[i], zinfo[i] = r['z'], r['status'], r
            hist.append(s[i])
    return {'s': s, 's_raw': s_raw, 'z': z, 'z_raw': z_raw, 'z_status': zst, 'z_info': zinfo}


# --- the layer -------------------------------------------------------------------------------------------------------

def owners(method, p):
    """name id -> borrower group: a ticker in method groups joins it; any other ticker is its own group; E3 is one."""
    owner = {e: g for g, es in method['groups'].items() for e in es}
    m = {t: owner.get(t, t) for leg in ('e1', 'e2') for t in p[leg]}
    m['E3'] = p.get('e3_group', 'Exposed lenders (E3)')
    return m


def layer_series(names, sessions, method):
    """One point per session (oldest first): Z (residual, scored), Z_raw (shown), groups, weights, cap, band."""
    p = method['layers']['L1']
    eng = method['engine']
    cut = config_cuts(method)
    tau = eng['tau']
    lw = {k: float(p['leg_weights'].get(k, 0)) for k in LEGS}
    target = sum(w for w in lw.values() if w > 0)
    own = owners(method, p)
    series = []
    for i, d in enumerate(sessions):
        live = {leg: [nm for nm in names if nm['leg'] == leg and nm['sc']['z'][i] is not None] for leg in LEGS}
        legs_live = [leg for leg in LEGS if live[leg] and lw[leg] > 0]
        share = sum(lw[leg] for leg in legs_live) / target if target else 0.0
        pt = {'session': d, 'available': None, 'live_leg_share': share, 'legs_live': legs_live}
        if not legs_live:
            pt.update(status='unavailable', Z=None, Z_raw=None, level=None, cap=None, cap_reason=None, groups={},
                      groups_stressed=[], weights=None, names={})
            series.append(pt)
            continue
        pt['available'] = max(nm['res'][i]['available'] for leg in legs_live for nm in live[leg])
        base = {nm['id']: lw[nm['leg']] / len(live[nm['leg']]) for leg in legs_live for nm in live[leg]}
        byid = {nm['id']: nm for leg in legs_live for nm in live[leg]}
        groups = {}
        for nid, w in base.items():
            g = groups.setdefault(own[nid], {'names': [], 'base': 0.0, 'zw': 0.0, 'zrw': 0.0, 'rw': 0.0})
            g['names'].append(nid)
            g['base'] += w
            g['zw'] += w * byid[nid]['sc']['z'][i]
            zr = byid[nid]['sc']['z_raw'][i]
            if zr is not None:
                g['zrw'] += w * zr
                g['rw'] += w
        for g in groups.values():
            g['z'] = g['zw'] / g['base']
            g['z_raw'] = g['zrw'] / g['rw'] if g['rw'] else None
            g['level'] = engine.logistic(g['z'], tau)
            g['band'] = engine.BANDS[engine.raw_band(g['level'], cut)]
        capw = engine.cap_weights({g: v['base'] for g, v in groups.items()}, eng['group_cap'])
        W = capw['weights']
        Z = sum(W[g] * v['z'] for g, v in groups.items())
        rw = sum(W[g] for g, v in groups.items() if v['z_raw'] is not None)
        Z_raw = sum(W[g] * v['z_raw'] for g, v in groups.items() if v['z_raw'] is not None) / rw if rw else None
        capb, why, hot = engine.two_group_cap({g: v['level'] for g, v in groups.items()}, cut, eng['min_groups_for_stressed'])
        nm_w = {nid: W[own[nid]] * base[nid] / groups[own[nid]]['base'] for nid in base}
        status = 'fresh' if share + 1e-12 >= eng['min_live_share'] else 'insufficient_live_weight'
        pt.update(status=status, Z=Z if status == 'fresh' else None, Z_raw=Z_raw if status == 'fresh' else None,
                  Z_unpublished=Z if status != 'fresh' else None,
                  level=engine.logistic(Z, tau) if status == 'fresh' else None,
                  cap=capb, cap_reason=why, groups_stressed=hot, weights=capw, groups=groups, names=nm_w, base=base)
        series.append(pt)
    h = eng['hysteresis']
    bands = engine.replay_bands(series, 'junior', cut, up_closes=h['junior_up_closes'], down_margin=h['down_margin'])
    for pt, b in zip(series, bands):
        pt.update(band=b['band'], raw_band=b['raw_band'], band_moved=b['moved'], band_reason=b['reason'])
    return series


def prepare(p):
    """Checks the L1 section beyond the schema. Returns (membership windows, list of all tickers). Raises ValueError."""
    bad = [k for k in p['leg_weights'] if k not in LEGS]
    if bad:
        raise ValueError(f'layers.L1.leg_weights: unknown leg(s) {", ".join(bad)} (known: {", ".join(LEGS)})')
    if sum(v for v in p['leg_weights'].values()) <= 0:
        raise ValueError('layers.L1.leg_weights: weights must not all be 0')
    if p['fit_min_obs'] > p['fit_sessions']:
        raise ValueError('layers.L1.fit_min_obs cannot exceed fit_sessions')
    if p['z_min_history'] > p['z_window']:
        raise ValueError('layers.L1.z_min_history cannot exceed z_window')
    fc = p['factors']
    for r in fc['rates']:
        if not FRED_ID.match(r):
            raise ValueError(f'layers.L1.factors.rates: {r!r} is not a FRED series id')
    _iso(p.get('price_start', '2018-01-01'))
    membership = {t: parse_windows(ws) for t, ws in (p.get('membership') or {}).items()}
    names = list(p['e1']) + list(p['e2'])
    if len(set(names)) != len(names):
        raise ValueError('layers.L1: a ticker is listed twice in e1/e2')
    tickers = list(fc['equity'])
    if fc['credit'] == 'hyg_ief':
        tickers += [HYG, IEF]
    if fc['vix'] == 'yahoo':
        tickers.append(VIX_YAHOO)
    if fc['btc'] == 'lagged':
        tickers.append(BTC)
    tickers += names + list(p['e3_lenders']) + [p['e3_benchmark']]
    seen, uniq = set(), []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return membership, uniq


def fred_ids(fc):
    ids = []
    if fc['credit'] == 'hy_oas':
        ids.append(HY_OAS)
    if fc['vix'] == 'fred':
        ids.append(VIX_FRED)
    return ids + list(fc['rates'])


def _r(x, k=4):
    return None if x is None else round(x, k)


def series_all(as_of, prices, fred, method):
    """Everything computed from loaded data: (sessions, factor names, factor rows, names, layer series)."""
    p = method['layers']['L1']
    fc = p['factors']
    membership, _ = prepare(p)
    start = p.get('price_start', '2018-01-01')
    cal = fc['equity'][0]
    sessions = sorted(d for d in prices.get(cal, {}) if start <= d <= as_of)
    fnames, rows = factor_rows(sessions, prices, fred, fc, as_of)
    X = [r['x'] for r in rows]
    btc = btc_lagged_col(prices.get(BTC, {}), sessions) if fc['btc'] == 'lagged' else None
    names = name_returns(p, prices, sessions, membership)
    for nm in names:
        if nm['leg'] == 'E2' and btc is not None:
            Xn = [None if x is None or b is None else x + [b] for x, b in zip(X, btc)]
            nm['factors'] = fnames + ['BTC-USD (lagged)']
        else:
            Xn = X
            nm['factors'] = fnames
        res = rolling_residuals(nm['y'], Xn, p['fit_sessions'], p['fit_min_obs'])
        for r, row in zip(res, rows):
            r['available'] = row['available']
            if r['status'] == 'missing' and row['missing']:
                r['why'] = row['missing']
        nm['res'] = res
        nm['sc'] = score_name(nm['y'], res, p)
    series = layer_series(names, sessions, method)
    return sessions, fnames, rows, names, series


def _name_out(nm, i, sessions, pt, own, p, membership):
    sc, res = nm['sc'], nm['res']
    last_ok = max((k for k in range(i + 1) if res[k]['status'] == 'ok'), default=None)
    zst = sc['z_status'][i]
    if sc['z'][i] is not None:
        st = 'fresh'
    elif zst == 'insufficient_history':
        st = 'insufficient_history'
    elif not nm['has_prices']:
        st = 'no_prices'
    elif nm['id'] in membership and not is_member(sessions[i], membership[nm['id']]):
        st = 'not_a_member'
    elif res[i]['status'] in ('no_fit', 'singular'):
        st = res[i]['status']
    else:
        st = 'no_value'
    q = {'leg': nm['leg'], 'group': own[nm['id']], 'status': st}
    if st == 'insufficient_history':
        zi = sc['z_info'][i]
        q['reason'] = f"{zi['n']} earlier 5-session sums, needs {zi['min_history']}"
    elif st == 'no_fit':
        q['reason'] = f"{res[i].get('n_fit')} valid returns in the previous {p['fit_sessions']} sessions, needs {p['fit_min_obs']}"
    elif st == 'not_a_member':
        q['reason'] = f"outside its membership window(s) {p['membership'][nm['id']]}"
    elif st == 'no_value':
        q['reason'] = res[i].get('why') or 'no return or residual on one of the last sessions'
    if nm['id'] == 'E3':
        q['lenders'] = list(p['e3_lenders'])
        q['benchmark'] = p['e3_benchmark']
        q['lenders_with_return'] = nm['constituents'][i]
        q['lenders_note'] = p.get('e3_lenders_note', E3_PLACEHOLDER)
    first = next((sessions[k] for k in range(len(sessions)) if nm['y'][k] is not None), None)
    q.update(first_return_session=first,
             last_residual_session=None if last_ok is None else sessions[last_ok],
             return_log=_r(nm['y'][i], 5), residual_log=_r(res[i].get('resid'), 5),
             s=_r(sc['s'][i], 5), s_raw=_r(sc['s_raw'][i], 5), z=_r(sc['z'][i], 3), z_raw=_r(sc['z_raw'][i], 3))
    zi = sc['z_info'][i]
    if zi and zi.get('status') == 'ok':
        q.update(z_history_n=zi['n'], z_median=_r(zi['median'], 5), z_scale=_r(zi['scale'], 5), z_floored=zi['floored'])
    if last_ok is not None:
        r = res[last_ok]
        q['fit'] = {'session': sessions[last_ok], 'n': r['n_fit'],
                    'beta': {k: _r(b, 4) for k, b in zip(['intercept'] + nm['factors'], r['beta'])}}
    w = (pt.get('names') or {}).get(nm['id'])
    if w is not None:
        q.update(weight=_r(w, 4), contribution=_r(w * sc['z'][i], 3),
                 raw_contribution=None if sc['z_raw'][i] is None else _r(w * sc['z_raw'][i], 3))
    return q


def snapshot(as_of, prices, fred, method):
    """The L1 result for as_of. `_series` holds the per-session points for composites (not published)."""
    p = method['layers']['L1']
    membership, _ = prepare(p)
    base = {'layer': 'L1', 'name': p['name'], 'kind': 'junior', 'build_date': as_of, 'as_of': None,
            'sources': {'prices': SOURCE_PRICES, 'factors_fred': SOURCE_FRED},
            'availability': ('equity closes available the same day; a FRED observation dated t on the next weekday; '
                             'the residual for t only once every factor for t is available'),
            'todo': TODO}
    cal = p['factors']['equity'][0]
    if not prices.get(cal):
        return dict(base, status='unavailable', reason=f'no prices for the calendar ticker {cal}', session=None,
                    available=None, Z=None, level=None, published=None, band=None, _series=[])
    sessions, fnames, rows, names, series = series_all(as_of, prices, fred, method)
    if not sessions:
        return dict(base, status='unavailable', reason='no sessions on or before the as-of date', session=None,
                    available=None, Z=None, level=None, published=None, band=None, _series=[])
    idx = [k for k, pt in enumerate(series) if pt['status'] != 'unavailable']
    last_row = max((k for k, r in enumerate(rows) if r['x'] is not None), default=None)
    fac = {'names': fnames, 'credit': p['factors']['credit'], 'vix': p['factors']['vix'], 'btc_for_e2': p['factors']['btc'],
           'last_price_session': sessions[-1],
           'last_complete_factor_session': None if last_row is None else sessions[last_row],
           'last_complete_factor_available': None if last_row is None else rows[last_row]['available'],
           'latest_session_status': rows[-1]['missing'] or 'complete',
           'closed_flags_last_session': rows[last_row]['flags'] if last_row is not None else []}
    base.update(factors=fac, last_price_session=sessions[-1])
    if not idx:
        return dict(base, status='unavailable', reason='no residual session yet (fit and z history still warming up)',
                    session=None, available=None, Z=None, level=None, published=None, band=None,
                    _series=[{'session': s['session'], 'Z': None, 'status': s['status']} for s in series])
    i = idx[-1]
    pt = series[i]
    own = owners(method, p)
    status = pt['status']
    if status == 'fresh' and pt['available'] != as_of:
        status = 'carried'
    per = {nm['id']: _name_out(nm, i, sessions, pt, own, p, membership) for nm in names}
    groups, attribution = {}, {}
    for g, v in pt['groups'].items():
        groups[g] = {'names': v['names'], 'z': _r(v['z'], 3), 'z_raw': _r(v['z_raw'], 3), 'level': _r(v['level'], 2),
                     'band': v['band']}
    if pt['weights']:
        W = pt['weights']
        for g, v in pt['groups'].items():
            attribution[g] = {'weight': _r(W['weights'][g]), 'contribution': _r(W['weights'][g] * v['z'], 3),
                              'raw_contribution': None if v['z_raw'] is None else _r(W['weights'][g] * v['z_raw'], 3),
                              'uncapped_weight': _r(W['uncapped'][g]), 'uncapped_contribution': _r(W['uncapped'][g] * v['z'], 3)}
    legs = {}
    byid = {nm['id']: nm for nm in names}
    nw = pt.get('names') or {}
    for leg in LEGS:
        ids = [nm['id'] for nm in names if nm['leg'] == leg]
        live = [x for x in ids if x in nw]
        zs = [byid[x]['sc']['z'][i] for x in live]
        zr = [byid[x]['sc']['z_raw'][i] for x in live]
        rec = {'weight_configured': p['leg_weights'].get(leg, 0), 'names': ids, 'names_live': live,
               'weight_effective': _r(sum(nw[x] for x in live)),
               'contribution': _r(sum(nw[x] * z for x, z in zip(live, zs)), 3),
               'raw_contribution': _r(sum(nw[x] * z for x, z in zip(live, zr) if z is not None), 3),
               'z_mean_live': _r(sum(zs) / len(zs), 3) if zs else None}
        if leg == 'E1' and live == ['ORCL']:
            rec['label'] = 'effectively Oracle alone on this session'
        legs[leg] = rec
    lv = pt['level']
    fac_ret = {t: ret_col(prices.get(t, {}), sessions) for t in p['factors']['equity']}
    leg_z = {leg: [nm for nm in names if nm['leg'] == leg] for leg in LEGS}

    def _mean(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    out = dict(base, status=status, session=pt['session'], as_of=pt['session'], available=pt['available'],
               Z=_r(pt['Z'], 3), level=_r(lv, 2), published=engine.published(lv),
               Z_raw=_r(pt['Z_raw'], 3), level_raw=_r(engine.logistic(pt['Z_raw'], method['engine']['tau']), 2) if pt['Z_raw'] is not None else None,
               raw_note='Z_raw: the same layer on raw (not residualized) returns; shown, not scored',
               raw_band=pt['raw_band'], band=pt['band'],
               band_status='fresh' if status == 'fresh' else 'held (no fresh value: the band cannot move)',
               band_moved=pt['band_moved'], band_reason=pt['band_reason'],
               two_group={'cap': pt['cap'], 'reason': pt['cap_reason'], 'groups_stressed_or_worse': pt['groups_stressed']},
               cap_infeasible=bool(pt['weights'] and pt['weights']['cap_infeasible']),
               live_leg_share=_r(pt['live_leg_share'], 3), legs_live=pt['legs_live'],
               groups_live=len(pt['groups']), sessions_with_value=sum(1 for s in series if s['status'] == 'fresh'),
               first_session_with_value=next((s['session'] for s in series if s['status'] == 'fresh'), None),
               legs=legs, groups=groups, attribution=attribution, names=per,
               # up to the last session with a value: later sessions are still waiting for their factors
               _series=[{'session': s['session'], 'available': s['available'],
                         'known': s['available'] or s['session'], 'Z': s['Z'], 'level': s['level'],
                         'status': s['status'], 'band': s.get('band'), 'raw_band': s.get('raw_band'),
                         'band_moved': s.get('band_moved'), 'groups_stressed': s.get('groups_stressed') or [],
                         'legs_z': {leg: _mean(nm['sc']['z'][k] for nm in leg_z[leg]) for leg in LEGS},
                         'legs_z_raw': {leg: _mean(nm['sc']['z_raw'][k] for nm in leg_z[leg]) for leg in LEGS},
                         'factor_returns': {t: c[k] for t, c in fac_ret.items()}}
                        for k, s in enumerate(series[:i + 1])])
    if pt['status'] != 'fresh':
        out['reason'] = (f"live leg weight {pt['live_leg_share']:.0%} of target, needs "
                         f"{method['engine']['min_live_share']:.0%}")
        out['Z_unpublished'] = _r(pt.get('Z_unpublished'), 3)
    return out


def _stub(as_of, p, status, reason):
    return {'layer': 'L1', 'name': p['name'], 'kind': 'junior', 'build_date': as_of, 'as_of': None, 'status': status, 'reason': reason,
            'available': None, 'Z': None, 'level': None, 'published': None, 'band': None, '_series': []}


def compute(as_of, method, ctx):
    """Build-facing entry point: (result, inputs). ctx: {'root', 'offline', optional 'price_downloader', 'opener'}."""
    p = method['layers']['L1']
    root = pathlib.Path(ctx.get('root') or ROOT)
    offline = bool(ctx.get('offline'))
    try:
        _, tickers = prepare(p)
    except ValueError as e:
        return _stub(as_of, p, 'config_error', str(e)), []
    prices, pin, pnotes = load_prices(tickers, as_of, root, offline, p.get('price_start', '2018-01-01'),
                                      ctx.get('price_downloader'))
    fred, fin, fnotes = load_fred(fred_ids(p['factors']), as_of, root, offline, ctx.get('opener'))
    missing_fred = [s for s in fred_ids(p['factors']) if s not in fred]
    if missing_fred:
        res = _stub(as_of, p, 'unavailable', f'FRED series missing: {", ".join(missing_fred)}')
    else:
        res = snapshot(as_of, prices, fred, method)
    res['load_notes'] = pnotes + fnotes
    return res, pin + fin


__all__ = ['next_weekday', 'parse_windows', 'parse_prices', 'parse_fred', 'load_prices', 'load_fred', 'ret_col',
           'fred_change_col', 'btc_lagged_col', 'factor_rows', 'solve', 'ols', 'rolling_residuals', 'score_name',
           'layer_series', 'series_all', 'snapshot', 'compute']
