"""Deterministic, conservative reconstruction of a bounded DTCC event window.

References may point to the initial report or its previous correction. Unknown
ancestors are not invented; conflicting branches are quarantined. This is a
research ledger, not proof that all historical lifecycle events were received.
"""
import datetime as dt
import json
from collections import Counter, defaultdict

REQUIRED_COLUMNS = (
    'Dissemination Identifier', 'Original Dissemination Identifier', 'Action type',
    'Event type', 'Event timestamp', 'Execution Timestamp', 'Expiration Date',
    'Underlying Asset Name', 'UPI Underlier Name', 'Underlier ID-Leg 1',
    'Fixed rate-Leg 1', 'Spread-Leg 1', 'Notional amount-Leg 1',
    'Notional currency-Leg 1', 'Other payment amount', 'Other payment type',
    'Other payment currency', 'UPI FISN', 'Package indicator',
    'Non-standardized term indicator', 'Post-priced swap indicator',
)
ACTIONS = {'NEWT', 'CORR', 'EROR', 'MODI', 'TERM'}


class SourceSchemaError(ValueError):
    pass


def validate_columns(columns):
    columns = columns or []
    missing = sorted(set(REQUIRED_COLUMNS) - set(columns))
    if missing or len(columns) != len(set(columns)):
        raise SourceSchemaError('DTCC schema mismatch: missing=' + ', '.join(missing) +
                                ('; duplicate columns' if len(columns) != len(set(columns)) else ''))


def validate_rows(rows):
    for row in rows:
        if any(str(row.get(k, '')).strip().upper() not in ('', 'TRUE', 'FALSE') for k in
               ('Package indicator', 'Non-standardized term indicator', 'Post-priced swap indicator')):
            raise SourceSchemaError('DTCC indicator outside documented blank/TRUE/FALSE domain')


def _stamp(value):
    try:
        t = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return t.timestamp() if t.tzinfo else None
    except (ValueError, TypeError):
        return None


def resolve_lifecycle(rows, entity_of):
    """Return effective tracked rows and aggregate diagnostics (no raw records).

    A cancellation anywhere in a lineage withholds the whole lineage, rather
    than resurrecting an older version. MODI/TERM connect references but cannot
    create a new historical transaction price. Parallel corrections of the
    initial id require distinct valid event timestamps; file/row order and id
    magnitude never decide price.
    """
    variants, edges, missing_ids = defaultdict(list), defaultdict(set), []
    for row in rows:
        action = str(row.get('Action type', '')).strip().upper()
        if action not in ACTIONS:
            if entity_of(row):
                missing_ids.append('unknown_action')
            continue
        identifier = str(row.get('Dissemination Identifier') or '').strip()
        if not identifier:
            if entity_of(row):
                missing_ids.append('missing_identifier')
            continue
        variants[identifier].append(row)
        parent = str(row.get('Original Dissemination Identifier') or '').strip()
        if action != 'NEWT' and parent:
            edges[identifier].add(parent)
            edges[parent].add(identifier)
        else:
            edges[identifier]
    issues = Counter(missing_ids)
    out, visited, quarantined, orphans, cancelled = [], set(), 0, 0, 0
    for start in sorted(edges):
        if start in visited:
            continue
        stack, component = [start], set()
        while stack:
            identifier = stack.pop()
            if identifier in component:
                continue
            component.add(identifier)
            stack.extend(edges[identifier] - component)
        visited.update(component)
        records = [r for identifier in component for r in variants[identifier]]
        if not any(entity_of(r) for r in records):
            continue
        reasons, nodes = set(), {}
        for identifier in component:
            versions = variants[identifier]
            if not versions:
                continue
            unique = {json.dumps(r, sort_keys=True, separators=(',', ':')) for r in versions}
            if len(unique) > 1:
                reasons.add('conflicting_identifier')
            nodes[identifier] = versions[0]
        roots = [i for i, r in nodes.items() if r.get('Action type') == 'NEWT']
        if len(roots) != 1:
            reasons.add('orphan_lineage' if not roots else 'multiple_roots')
        parents = {i: str(r.get('Original Dissemination Identifier') or '').strip()
                   for i, r in nodes.items() if r.get('Action type') != 'NEWT'}
        for identifier in nodes:
            seen, current = set(), identifier
            while current in parents:
                if current in seen:
                    reasons.add('cyclic_lineage')
                    break
                seen.add(current)
                current = parents[current]
                if not current or current not in nodes:
                    reasons.add('orphan_lineage')
                    break
        # No cancellation may be lost even if its row no longer names a tracked issuer.
        if any(r.get('Action type') == 'EROR' for r in nodes.values()):
            cancelled += 1
            if not reasons:
                continue
        prices = {i: r for i, r in nodes.items() if r.get('Action type') in ('NEWT', 'CORR')}
        def ancestor(a, b):
            seen = set()
            while b in parents and b not in seen:
                seen.add(b)
                b = parents[b]
                if b == a:
                    return True
            return False
        leaves = [i for i in prices if not any(i != j and ancestor(i, j) for j in prices)]
        if len(leaves) > 1:
            stamps = {i: _stamp(prices[i].get('Event timestamp')) for i in leaves}
            if any(x is None for x in stamps.values()) or len(set(stamps.values())) != len(stamps):
                reasons.add('ambiguous_correction_order')
            else:
                leaves = [max(stamps, key=stamps.get)]
        if reasons:
            issues.update(reasons)
            quarantined += 1
            orphans += int('orphan_lineage' in reasons)
            continue
        if leaves and entity_of(prices[leaves[0]]):
            out.append(prices[leaves[0]])
    out.sort(key=lambda r: (str(r.get('Execution Timestamp', '')), str(r.get('Dissemination Identifier', ''))))
    return out, {'status': 'partial' if issues else 'complete', 'issue_counts': dict(sorted(issues.items())),
                 'quarantined_lineages': quarantined, 'orphan_lineages': orphans,
                 'cancelled_lineages': cancelled, 'effective_trades': len(out),
                 'bounded_history': True, 'point_in_time_verified': False}
