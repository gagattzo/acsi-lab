"""Cached, retrying HTTP GET (stack/fetch.py), with a fake opener: no network."""
import hashlib, io, pathlib, sys, urllib.error
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import fetch  # noqa: E402

URL = 'https://example.test/data/file.csv'


class Resp:
    def __init__(self, b):
        self.b = b

    def read(self):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    """Replays a script of bodies (bytes) or exceptions, one per call."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def __call__(self, req, timeout):
        self.calls.append(req)
        x = self.script.pop(0)
        if isinstance(x, BaseException):
            raise x
        return Resp(x)


def http_error(code):
    return urllib.error.HTTPError(URL, code, 'err', {}, io.BytesIO(b''))


def csv_ok(b):
    return b.startswith(b'date,')


GOOD = b'date,value\n2026-11-25,1\n'
NOSLEEP = {'sleep': lambda s: None}


def files(p):
    return sorted(x.relative_to(p).as_posix() for x in p.rglob('*') if x.is_file())


def test_download_is_written_atomically_and_hashed(tmp_path):
    op = Opener(GOOD)
    r = fetch.get(URL, 'fred', 'X.csv', validate=csv_ok, root=tmp_path, opener=op, **NOSLEEP)
    assert r['ok'] and r['status'] == 'downloaded' and r.data == GOOD
    assert r['sha256'] == hashlib.sha256(GOOD).hexdigest() and r['bytes'] == len(GOOD)
    assert files(tmp_path) == ['fred/X.csv'] and (tmp_path / 'fred' / 'X.csv').read_bytes() == GOOD
    assert op.calls[0].get_header('User-agent').startswith('Mozilla/5.0 (compatible; acsi-lab')


def test_cached_file_is_used_without_a_request(tmp_path):
    fetch.get(URL, 'fred', 'X.csv', validate=csv_ok, root=tmp_path, opener=Opener(GOOD), **NOSLEEP)
    op = Opener()
    r = fetch.get(URL, 'fred', 'X.csv', validate=csv_ok, root=tmp_path, opener=op, **NOSLEEP)
    assert r['status'] == 'cached' and r.data == GOOD and op.calls == []


def test_failure_is_never_cached_and_is_retried_next_run(tmp_path):
    op = Opener(OSError('reset'), OSError('reset'), OSError('reset'))
    r = fetch.get(URL, 'fred', 'X.csv', validate=csv_ok, root=tmp_path, opener=op, retries=3, **NOSLEEP)
    assert not r['ok'] and r['status'] == 'failed' and r['attempts'] == 3 and 'reset' in r['error']
    assert files(tmp_path) == ['fred'] or files(tmp_path) == []
    r = fetch.get(URL, 'fred', 'X.csv', validate=csv_ok, root=tmp_path, opener=Opener(GOOD), **NOSLEEP)
    assert r['status'] == 'downloaded' and files(tmp_path) == ['fred/X.csv']


def test_payload_rejected_by_the_validator_is_retried_and_not_written(tmp_path):
    op = Opener(b'<html>rate limited</html>', b'', GOOD)
    r = fetch.get(URL, 'fred', 'X.csv', validate=csv_ok, root=tmp_path, opener=op, retries=3, **NOSLEEP)
    assert r['status'] == 'downloaded' and r['attempts'] == 3 and r.data == GOOD
    op = Opener(b'<html>no</html>', b'<html>no</html>')
    r = fetch.get(URL, 'fred', 'Y.csv', validate=csv_ok, root=tmp_path, opener=op, retries=2, **NOSLEEP)
    assert not r['ok'] and 'validator' in r['error'] and not (tmp_path / 'fred' / 'Y.csv').exists()
    assert not [x for x in files(tmp_path) if x.endswith('.part')]


def test_a_validator_that_throws_rejects_the_payload(tmp_path):
    def boom(b):
        raise ValueError('bad')
    r = fetch.get(URL, 's', 'f', validate=boom, root=tmp_path, opener=Opener(GOOD), retries=1, **NOSLEEP)
    assert not r['ok'] and files(tmp_path) in ([], ['s'])


def test_backoff_grows_between_retries(tmp_path):
    slept = []
    op = Opener(OSError('x'), OSError('x'), GOOD)
    fetch.get(URL, 's', 'f', root=tmp_path, opener=op, retries=3, backoff=1.5, sleep=slept.append)
    assert slept == [1.5, 3.0]


def test_http_404_is_not_retried_but_503_is(tmp_path):
    op = Opener(http_error(404), GOOD)
    r = fetch.get(URL, 's', 'a', root=tmp_path, opener=op, retries=3, **NOSLEEP)
    assert not r['ok'] and r['attempts'] == 1 and r['error'] == 'HTTP 404'
    op = Opener(http_error(503), http_error(429), GOOD)
    r = fetch.get(URL, 's', 'b', root=tmp_path, opener=op, retries=3, **NOSLEEP)
    assert r['ok'] and r['attempts'] == 3


def test_offline_never_touches_the_network(tmp_path):
    def no_network(req, timeout):
        raise AssertionError('network used offline')
    r = fetch.get(URL, 's', 'f', root=tmp_path, opener=no_network, offline=True)
    assert not r['ok'] and r['status'] == 'offline_missing' and files(tmp_path) == []
    fetch.get(URL, 's', 'f', root=tmp_path, opener=Opener(GOOD), **NOSLEEP)
    r = fetch.get(URL, 's', 'f', root=tmp_path, opener=no_network, offline=True)
    assert r['ok'] and r['status'] == 'cached' and r.data == GOOD
    r = fetch.get(URL, 's', 'f', root=tmp_path, opener=no_network, offline=True, refresh=True)
    assert r['ok'] and r['status'] == 'stale_cache'


def test_invalid_cached_file_is_ignored_and_replaced(tmp_path):
    p = tmp_path / 's' / 'f'
    p.parent.mkdir(parents=True)
    p.write_bytes(b'')
    r = fetch.get(URL, 's', 'f', validate=csv_ok, root=tmp_path, opener=Opener(GOOD), **NOSLEEP)
    assert r['status'] == 'downloaded' and p.read_bytes() == GOOD
    p.write_bytes(b'<html>')
    assert fetch.get(URL, 's', 'f', validate=csv_ok, root=tmp_path, offline=True)['status'] == 'offline_missing'


def test_refresh_falls_back_to_the_cache_and_keeps_it(tmp_path):
    fetch.get(URL, 's', 'f', root=tmp_path, opener=Opener(GOOD), **NOSLEEP)
    r = fetch.get(URL, 's', 'f', root=tmp_path, opener=Opener(OSError('down')), refresh=True, retries=1, **NOSLEEP)
    assert r['ok'] and r['status'] == 'stale_cache' and r['error'] and (tmp_path / 's' / 'f').read_bytes() == GOOD
    new = b'date,value\n2026-11-26,2\n'
    r = fetch.get(URL, 's', 'f', root=tmp_path, opener=Opener(new), refresh=True, **NOSLEEP)
    assert r['status'] == 'refreshed' and (tmp_path / 's' / 'f').read_bytes() == new


def test_unsafe_cache_names_are_rejected(tmp_path):
    for bad in ('../x', '/etc/passwd', ''):
        with pytest.raises(ValueError):
            fetch.get(URL, 's', bad, root=tmp_path, opener=Opener(GOOD), **NOSLEEP)


def test_min_interval_spaces_requests_to_one_host(tmp_path):
    t = [100.0]
    slept = []

    def sleep(s):
        slept.append(round(s, 6))
        t[0] += s
    clock = lambda: t[0]  # noqa: E731
    fetch._last.clear()
    for i in range(3):
        fetch.get(URL, 's', f'f{i}', root=tmp_path, opener=Opener(GOOD), min_interval=0.125, sleep=sleep, clock=clock)
    assert slept == [0.125, 0.125]
