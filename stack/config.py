"""Load and validate a Stack method file (stack/stack-v0.yaml) against schema/stack_method.yaml.

Rejects unknown keys, missing keys, wrong types, out-of-range values, duplicate YAML keys, and inconsistent settings
(bands out of order, an L2 entity with no borrower group, a composite naming an unknown layer).

  python -m stack.config [stack/stack-v0.yaml]      exit 1 and list every error if the file is rejected
"""
import hashlib, pathlib, sys
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT = ROOT / 'stack' / 'stack-v0.yaml'
SCHEMA = ROOT / 'schema' / 'stack_method.yaml'


class ConfigError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('stack method rejected:\n  ' + '\n  '.join(self.errors))


class _StrictLoader(yaml.SafeLoader):
    pass


def _no_duplicates(loader, node, deep=False):
    seen = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f'duplicate key {key!r}', k.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check(value, spec, sections, where, errs):
    t = spec['type']
    if t in sections:
        if not isinstance(value, dict):
            errs.append(f'{where}: must be a mapping')
            return
        sec = sections[t]
        for k in value:
            if k not in sec['keys']:
                errs.append(f'{where}: unknown key {k!r}')
        for k in sec.get('required', []):
            if k not in value:
                errs.append(f'{where}: missing key {k!r}')
        for k, v in value.items():
            if k in sec['keys']:
                _check(v, sec['keys'][k], sections, f'{where}.{k}', errs)
        return
    if t == 'str':
        ok = isinstance(value, str)
    elif t == 'bool':
        ok = isinstance(value, bool)
    elif t == 'int':
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif t == 'number':
        ok = _is_num(value)
    elif t == 'enum':
        ok = value in spec['values']
        if not ok:
            errs.append(f'{where}: {value!r} is not one of {spec["values"]}')
            return
    elif t == 'list_str':
        ok = isinstance(value, list) and bool(value) and all(isinstance(x, str) for x in value)
    elif t == 'map_number':
        ok = isinstance(value, dict) and all(isinstance(k, str) and _is_num(v) for k, v in value.items())
        if ok:
            for k, v in value.items():
                if ('min' in spec and v < spec['min']) or ('max' in spec and v > spec['max']):
                    errs.append(f'{where}.{k}: {v} outside {spec.get("min")}..{spec.get("max")}')
            return
    elif t == 'groups':
        ok = (isinstance(value, dict) and bool(value)
              and all(isinstance(k, str) and isinstance(v, list) and v and all(isinstance(x, str) for x in v) for k, v in value.items()))
    else:
        errs.append(f'{where}: schema type {t!r} unknown')
        return
    if not ok:
        errs.append(f'{where}: must be {t}, got {value!r}')
        return
    if t in ('int', 'number'):
        if 'min' in spec and value < spec['min']:
            errs.append(f'{where}: {value} is below {spec["min"]}')
        if 'max' in spec and value > spec['max']:
            errs.append(f'{where}: {value} is above {spec["max"]}')


def _consistency(m, errs):
    e = m.get('engine') or {}
    b = e.get('bands') or {}
    cuts = [b.get(k) for k in ('normal', 'watch', 'stressed', 'severe')]
    if all(_is_num(c) for c in cuts) and not all(x < y for x, y in zip(cuts, cuts[1:])):
        errs.append('engine.bands: cuts must rise strictly: normal < watch < stressed < severe')
    s = e.get('staleness') or {}
    if _is_num(s.get('full_weight_days')) and _is_num(s.get('zero_weight_days')) and s['zero_weight_days'] <= s['full_weight_days']:
        errs.append('engine.staleness: zero_weight_days must exceed full_weight_days')
    groups = m.get('groups') if isinstance(m.get('groups'), dict) else {}
    owner = {}
    for g, ents in groups.items():
        for x in ents if isinstance(ents, list) else []:
            if x in owner:
                errs.append(f'groups: entity {x} is in both {owner[x]} and {g}')
            owner[x] = g
    layers = m.get('layers') if isinstance(m.get('layers'), dict) else {}
    l2 = layers.get('L2') if isinstance(layers.get('L2'), dict) else {}
    for x in l2.get('entities') or []:
        if x not in owner:
            errs.append(f'layers.L2.entities: {x} has no borrower group in groups')
    if len(set(l2.get('entities') or [])) != len(l2.get('entities') or []):
        errs.append('layers.L2.entities: duplicate entity')
    W = l2.get('window_sessions')
    for k in ('min_sessions_with_prints', 'max_sessions_since_print'):
        if isinstance(W, int) and isinstance(l2.get(k), int) and l2[k] > W:
            errs.append(f'layers.L2.{k}: cannot exceed window_sessions ({W})')
    if isinstance(l2.get('z_min_history'), int) and isinstance(l2.get('z_window'), int) and l2['z_min_history'] > l2['z_window']:
        errs.append('layers.L2.z_min_history: cannot exceed z_window')
    for name, c in (m.get('composites') or {}).items():
        lw = c.get('layers') if isinstance(c, dict) else None
        if not isinstance(lw, dict):
            continue
        for k in lw:
            if k not in layers:
                errs.append(f'composites.{name}.layers: unknown layer {k}')
        if lw and sum(v for v in lw.values() if _is_num(v)) <= 0:
            errs.append(f'composites.{name}.layers: weights must not all be 0')
    for name, layer_ids in (('junior', ('L1', 'L2')), ('senior', ('L3', 'L4'))):
        lw = ((m.get('composites') or {}).get(name) or {}).get('layers')
        if isinstance(lw, dict):
            for k in lw:
                if k in layers and isinstance(layers[k], dict) and layers[k].get('kind') != name:
                    errs.append(f'composites.{name}: layer {k} is {layers[k].get("kind")}, not {name}')


def validate(m, schema=None):
    """List of errors (empty when the method is valid)."""
    spec = schema if schema is not None else yaml.safe_load(open(SCHEMA))
    errs = []
    if not isinstance(m, dict):
        return ['method file must be a mapping']
    _check(m, {'type': 'top'}, spec['sections'], 'method', errs)
    if not errs:
        _consistency(m, errs)
    return errs


def load(path=None):
    """(method dict, sha256 of the file bytes). Raises ConfigError listing every problem."""
    p = pathlib.Path(path) if path else DEFAULT
    raw = p.read_bytes()
    try:
        m = yaml.load(raw, Loader=_StrictLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as e:
        raise ConfigError([f'{p}: not valid YAML: {e}']) from e
    errs = validate(m)
    if errs:
        raise ConfigError(errs)
    return m, hashlib.sha256(raw).hexdigest()


def cuts(m):
    b = m['engine']['bands']
    return (float(b['normal']), float(b['watch']), float(b['stressed']), float(b['severe']))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        m, h = load(path)
    except ConfigError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    print(f'ok: {m["name"]} {m["version"]} (sha256 {h})')


if __name__ == '__main__':
    main()
