"""Shared engine for the Stack index (METHODOLOGIES section 2). Plain functions on plain data, no I/O.

  robust_z            (x - median of the previous N obs) / max(1.4826 * MAD, floor)
  anchor_z            (x - a) / s, signed so that higher always means more stress
  logistic, published level = 100 / (1 + exp(-Z / tau)); the published integer is held inside 1..99
  raw_band, replay_bands
                      Calm < 35, Normal 35-50, Watch 50-65, Stressed 65-80, Severe >= 80, with hysteresis replayed over
                      the history up to the as-of date, so the band is deterministic
  staleness_weight    quarterly facts: full weight to 120 days after period end, linear to 0 at 180 (never decayed)
  composite           weighted Z over live parts, published only when >= 70% of the target weight is live
  two_group_cap       a layer is Stressed or Severe only if >= 2 borrower groups are themselves Stressed or worse
  cap_weights         20% group cap with pro-rata redistribution; equal weights and cap_infeasible below 5 groups
  depth               count of layers at Stressed or worse
  weekly_stack        optional weekly Stack Z (not adopted)

Value statuses: fresh (new information today), carried (an older value repeated), stale (too old to use). Only fresh
values can move a band. Cutpoints, windows and weights are proposed examples, not calibrated.
"""
import math, statistics

BANDS = ('Calm', 'Normal', 'Watch', 'Stressed', 'Severe')
DEFAULT_CUTS = (35.0, 50.0, 65.0, 80.0)  # lower edge of Normal, Watch, Stressed, Severe
STRESSED = BANDS.index('Stressed')
LIVE = ('fresh', 'carried')  # statuses that carry a usable value into a composite


# --- scoring one input -----------------------------------------------------------------------------------------------

def robust_z(x, history, window=252, min_history=60, floor=1e-9):
    """z of x against the previous `window` observations in `history` (oldest first, x itself not included).
    Returns a dict: status ok or insufficient_history, z, median, scale, n, floored (the floor set the scale)."""
    if floor is None or floor <= 0:
        raise ValueError('floor must be positive')
    hist = [h for h in history if h is not None][-window:]
    if x is None or len(hist) < min_history:
        return {'status': 'insufficient_history', 'z': None, 'n': len(hist), 'min_history': min_history}
    med = statistics.median(hist)
    mad = statistics.median([abs(h - med) for h in hist])
    raw = 1.4826 * mad
    scale = max(raw, floor)
    return {'status': 'ok', 'z': (x - med) / scale, 'median': med, 'scale': scale, 'n': len(hist),
            'floored': raw < floor}


def anchor_z(x, anchor, scale, higher_is_stress=True):
    """Pre-registered economic anchor: z = (x - a) / s, sign flipped when a lower x means more stress.
    Example: loan FV / cost with a = 0.97, s = 0.03 and higher_is_stress=False scores 0.91 as z = +2."""
    if scale is None or scale <= 0:
        raise ValueError('scale must be positive')
    if x is None:
        return None
    z = (x - anchor) / scale
    return z if higher_is_stress else -z


def logistic(Z, tau=2.0):
    """Display level 0..100 (exclusive). No clip: Z is stored unbounded for audit."""
    if tau <= 0:
        raise ValueError('tau must be positive')
    t = Z / tau
    if t >= 0:
        return 100.0 / (1.0 + math.exp(-t))
    e = math.exp(t)
    return 100.0 * e / (1.0 + e)


def published(level):
    """The published integer, held inside 1..99 so rounding never prints a false 0 or 100."""
    if level is None:
        return None
    return int(min(99, max(1, math.floor(level + 0.5))))


# --- bands and hysteresis --------------------------------------------------------------------------------------------

def raw_band(level, cuts=DEFAULT_CUTS):
    """Band index of a level with no hysteresis: 0 Calm .. 4 Severe."""
    b = 0
    for i, c in enumerate(cuts):
        if level >= c:
            b = i + 1
    return b


def replay_bands(obs, mode='junior', cuts=DEFAULT_CUTS, up_closes=2, up_releases=1, down_margin=4.0):
    """Replay the band state over a history, oldest first. Each observation is a dict with `level`, `status`
    (fresh, carried, stale or anything else) and optionally `cap` (a band name the state may not exceed, e.g. 'Watch'
    from the 2-group rule) and `cap_reason`.

    Rules:
      - only fresh observations count; carried, stale or missing ones change nothing (they cannot trigger or clear a
        band) and break a run of consecutive closes
      - the first fresh observation sets the band to its raw band (there is no earlier state to hold)
      - up: junior needs `up_closes` consecutive fresh closes above the current band and moves to the lowest band all
        of them reached; senior moves on `up_releases` fresh release(s)
      - down: one fresh close at least `down_margin` points below the cut moves the band down, past every cut the
        level is at least `down_margin` below
      - a cap applies on fresh observations: the state is held at or below it, and the reason is kept
    Returns a list of dicts (one per observation): band, band_index, raw_band, moved (up/down/capped/None), reason."""
    need_up = up_closes if mode == 'junior' else up_releases
    if mode not in ('junior', 'senior'):
        raise ValueError('mode must be junior or senior')
    state, run, out = None, [], []
    for o in obs:
        st, lv = o.get('status'), o.get('level')
        rec = {'raw_band': None, 'moved': None, 'reason': None}
        if st != 'fresh' or lv is None:
            run = []
            rec['reason'] = f'{st or "missing"} value: band unchanged'
            out.append(dict(rec, band_index=state, band=None if state is None else BANDS[state]))
            continue
        rb = raw_band(lv, cuts)
        rec['raw_band'] = BANDS[rb]
        cap = BANDS.index(o['cap']) if o.get('cap') else None
        target = rb if cap is None else min(rb, cap)
        if state is None:
            state, run = target, []
            rec['moved'], rec['reason'] = 'init', 'first fresh value sets the band'
        elif target > state:
            run.append(target)
            if len(run) >= need_up:
                state = min(run[-need_up:])
                run = []
                rec['moved'] = 'up'
                rec['reason'] = f'{need_up} consecutive fresh {"closes" if mode == "junior" else "release(s)"} above the band'
            else:
                rec['reason'] = f'above the band, {len(run)} of {need_up} fresh closes'
        else:
            run = []
            down = sum(1 for c in cuts if lv > c - down_margin)  # below a cut only when at least the margin below
            if down < state:
                state = down
                rec['moved'], rec['reason'] = 'down', f'{down_margin:g} points or more below the cut'
        if cap is not None and (state > cap or rb > cap):
            rec['cap_reason'] = o.get('cap_reason') or f'capped at {BANDS[cap]}'
            if state > cap:
                state = cap
                rec['moved'], rec['reason'] = 'capped', rec['cap_reason']
            elif rec['reason'] is None:
                rec['reason'] = rec['cap_reason']
        out.append(dict(rec, band_index=state, band=BANDS[state]))
    return out


# --- staleness, composites, groups -----------------------------------------------------------------------------------

def staleness_weight(age_days, full_days=120, zero_days=180):
    """Weight of a quarterly fact `age_days` after its period end: 1 up to full_days, linear to 0 at zero_days.
    Only the weight falls; the value itself is never moved toward neutral."""
    if zero_days <= full_days:
        raise ValueError('zero_days must exceed full_days')
    if age_days is None:
        return 0.0
    if age_days <= full_days:
        return 1.0
    if age_days >= zero_days:
        return 0.0
    return (zero_days - age_days) / (zero_days - full_days)


def composite(parts, weights, min_live_share=0.7, tau=2.0):
    """Weighted Z over the live parts. parts: {name: {'z': float|None, 'status': str, 'weight_factor': 0..1}}.
    weights: {name: target weight}; a weight of 0 means shown, not scored. Z = sum(w z) / sum(w) over live parts.
    Published only when live weight / target weight >= min_live_share; the live share is always shown."""
    target = sum(w for w in weights.values() if w > 0)
    live, used = 0.0, {}
    for k, w in weights.items():
        p = parts.get(k) or {}
        if w <= 0 or p.get('z') is None or p.get('status') not in LIVE:
            continue
        we = w * float(p.get('weight_factor', 1.0))
        if we <= 0:
            continue
        used[k] = we
        live += we
    share = live / target if target > 0 else 0.0
    out = {'target_weight': target, 'live_weight': live, 'live_share': share, 'min_live_share': min_live_share,
           'weights_used': {k: v / live for k, v in used.items()} if live else {}}
    if target <= 0 or share + 1e-12 < min_live_share or live <= 0:
        return dict(out, status='insufficient_live_weight', Z=None, level=None, published=None,
                    reason=f'live weight {share:.0%} of target, needs {min_live_share:.0%}')
    Z = sum(used[k] * parts[k]['z'] for k in used) / live
    lv = logistic(Z, tau)
    fresh = any(parts[k].get('status') == 'fresh' for k in used)
    return dict(out, status='fresh' if fresh else 'carried', Z=Z, level=lv, published=published(lv),
                contributions={k: used[k] / live * parts[k]['z'] for k in used})


def two_group_cap(group_levels, cuts=DEFAULT_CUTS, min_groups=2):
    """The 2-group rule. group_levels: {group: level or None}. Returns (cap band name or None, reason, groups at
    Stressed or worse). A layer can be Stressed or Severe only if at least min_groups groups are Stressed or worse."""
    hot = sorted(g for g, lv in group_levels.items() if lv is not None and raw_band(lv, cuts) >= STRESSED)
    if len(hot) >= min_groups:
        return None, None, hot
    why = (f'capped at Watch: {len(hot)} borrower group(s) at Stressed or worse '
           f'({", ".join(hot) if hot else "none"}), the rule needs {min_groups}')
    return 'Watch', why, hot


def cap_weights(weights, cap=0.2):
    """Group cap: no group carries more than `cap` of a layer. weights: {group: base weight >= 0}. The excess of a
    capped group is redistributed pro rata to the others, repeatedly, until none is above the cap. With fewer groups
    than 1/cap carrying weight the cap cannot hold: equal weights, cap_infeasible=True.
    Returns {'weights': capped (sum 1), 'uncapped': normalised base weights, 'capped': [groups], 'cap_infeasible'}."""
    pos = {g: float(w) for g, w in weights.items() if w and w > 0}
    tot = sum(pos.values())
    if not pos:
        return {'weights': {}, 'uncapped': {}, 'capped': [], 'cap_infeasible': False}
    base = {g: w / tot for g, w in pos.items()}
    if len(pos) * cap < 1 - 1e-9:
        eq = 1.0 / len(pos)
        return {'weights': {g: eq for g in pos}, 'uncapped': base, 'capped': [], 'cap_infeasible': True}
    w, fixed = dict(base), set()
    for _ in range(len(w) + 1):
        over = [g for g in w if g not in fixed and w[g] > cap + 1e-12]
        if not over:
            break
        for g in over:
            fixed.add(g)
        free = [g for g in w if g not in fixed]
        excess = 1.0 - cap * len(fixed)
        ftot = sum(base[g] for g in free)
        for g in fixed:
            w[g] = cap
        for g in free:
            w[g] = excess * base[g] / ftot if ftot > 0 else excess / len(free)
    return {'weights': w, 'uncapped': base, 'capped': sorted(fixed), 'cap_infeasible': False}


def depth(layer_bands):
    """Depth: how many layers are at Stressed or worse. layer_bands: {layer: band name or None}. A count, not a
    ladder: any layer may be stressed before any other. Layers without a band are listed, not counted."""
    hot = sorted(k for k, b in layer_bands.items() if b in BANDS and BANDS.index(b) >= STRESSED)
    unknown = sorted(k for k, b in layer_bands.items() if b not in BANDS)
    return {'depth': len(hot), 'layers_stressed_or_worse': hot, 'layers_without_band': unknown,
            'layers_with_band': len(layer_bands) - len(unknown)}


def weekly_stack(junior_week, senior_now, weights, min_live_share=0.7, tau=2.0):
    """Optional weekly Stack Z (not adopted). junior_week: {layer: [daily Z of fresh days in the week]} (averaged, so the
    headline is not driven by whichever layer updated last); senior_now: {layer: part dict as in composite()}."""
    parts = {}
    for k, zs in junior_week.items():
        zs = [z for z in zs if z is not None]
        parts[k] = {'z': sum(zs) / len(zs), 'status': 'fresh'} if zs else {'z': None, 'status': 'missing'}
    parts.update(senior_now)
    out = composite(parts, weights, min_live_share, tau)
    return dict(out, label='optional, not adopted')
