"""The DTCC parity corpus: one fixture, decoded identically here and on aistress.si (api/cds.js).

tests/fixtures/dtcc_parity holds synthetic records for every decoding rule (corpus.json), this decoder's output
(expected.json) and their hashes (SHA256SUMS). The website keeps byte-identical copies, checks its JavaScript decoder
against expected.json in CI, and checks the copies against this repository's main branch. These tests keep the fixture
honest on this side: rebuildable, non-empty, covering every scenario, and pinned to behaviour that must not drift.
"""
import hashlib
import importlib.util
import json
import pathlib

FIX = pathlib.Path(__file__).resolve().parent / 'fixtures' / 'dtcc_parity'
spec = importlib.util.spec_from_file_location('parity_build', FIX / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

corpus = json.loads((FIX / 'corpus.json').read_text())
expected = json.loads((FIX / 'expected.json').read_text())


def test_fixture_is_exactly_what_the_builder_produces():
    for name, text in build.build().items():
        assert (FIX / name).read_text() == text, f'{name} is stale or hand-edited; rebuild with tests/fixtures/dtcc_parity/build.py'


def test_hashes_match_the_files():
    sums = dict(line.split('  ')[::-1] for line in (FIX / 'SHA256SUMS').read_text().splitlines())
    assert set(sums) == {'corpus.json', 'expected.json'}
    for name, digest in sums.items():
        assert hashlib.sha256((FIX / name).read_bytes()).hexdigest() == digest


def test_decoder_reproduces_the_expected_output():
    assert build.decode(corpus) == expected
    assert expected['decoder_version'] == build.dtcc.DECODER_VERSION


def test_corpus_is_not_empty_and_covers_every_scenario():
    rows = [r for f in corpus['files'] for r in f['rows']]
    ids = {r['Dissemination Identifier'] for r in rows}
    assert len(corpus['files']) >= 5 and len(rows) >= 40 and len(corpus['scenarios']) >= 30
    assert all(r.keys() == set(corpus['columns']) for r in rows)
    for scenario, members in corpus['scenarios'].items():
        assert members and set(members) <= ids, scenario
    priced = [(d, e) for d, ents in expected['days'].items() for e, v in ents.items() if v['level']]
    assert len(expected['days']) >= 5 and len(priced) >= 9
    assert {e for _, e in priced} == {'ORCL', 'CRWV', 'META'}


def test_expected_output_pins_each_rule():
    days = expected['days']
    o23, c23, m23 = days['2026-11-23']['ORCL'], days['2026-11-23']['CRWV'], days['2026-11-23']['META']
    assert o23['level']['official_trades'] == 6 and o23['quality']['invalid_quotes'] == 2 and o23['quality']['basket_legs'] == 1
    assert {'quote_below_10bp', 'invalid_or_unresolved_quote_units', 'missing_price'} <= set(o23['quality']['reasons'])
    assert c23['level'] == {'official_bp': 4500.0, 'official_kind': 'quoted', 'official_trades': 1, 'quoted_trades': 1}  # no ceiling
    assert c23['quality']['ambiguous'] == 1 and c23['quality']['model_domain_errors'] == 1 and 'quote_below_10bp' in c23['quality']['reasons']
    assert m23['quality']['basket_legs'] == 1 and m23['quality']['ambiguous'] == 1
    # Correction (370) and late report (362) priced on the execution day; the cancelled trade, the ambiguous
    # correction order and the conflicting identifier are not.
    assert days['2026-11-24']['ORCL']['level'] == {'official_bp': 366.0, 'official_kind': 'quoted', 'official_trades': 2, 'quoted_trades': 2}
    # The 10 bp quote rule never decides an upfront: a low root under 10 bp leaves the trade ambiguous.
    c24 = days['2026-11-24']['CRWV']
    assert c24['level'] is None and c24['quality']['ambiguous'] == 1 and c24['quality']['candidate_interval_bp']['low'] < 10
    assert '2026-11-28' not in days  # weekend execution
    assert expected['ledger_quality']['issue_counts'] == {'ambiguous_correction_order': 1, 'conflicting_identifier': 1,
                                                         'cyclic_lineage': 1, 'orphan_lineage': 3, 'unknown_action': 1}
