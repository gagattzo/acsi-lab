"""Cached HTTP GET for the Stack layers. Raw payloads go to data/raw/<source>/<name> (gitignored), exactly as served.

  - a cached file that passes the validator is used as is; nothing is downloaded again unless refresh=True
  - only a payload that passes the validator is written, atomically (temporary file in the same directory, then
    rename), so a failed or partial download leaves nothing behind and is retried on the next run
  - transient failures (network errors, timeouts, HTTP 429 and 5xx, a body the validator rejects) are retried with
    exponential backoff; other HTTP errors (404 and the like) are not
  - offline=True never touches the network: a cached valid file or nothing
  - every result carries the sha256 of the bytes
  - min_interval spaces requests to one host (SEC asks for at most 10 per second; use 0.125 for 8)

The opener is injectable so tests run without a network: opener(request, timeout) returns an object with read(),
usable as a context manager, like urllib.request.urlopen.
"""
import hashlib, os, pathlib, tempfile, threading, time, urllib.error, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / 'data' / 'raw'
UA = 'Mozilla/5.0 (compatible; acsi-lab/0.2; +https://aistress.si/lab)'
RETRY_HTTP = {408, 425, 429, 500, 502, 503, 504}

_last = {}
_lock = threading.Lock()


class FetchResult(dict):
    """ok, status (cached, downloaded, refreshed, stale_cache, offline_missing, failed), path, sha256, bytes,
    url, attempts, error. data holds the payload (not part of the dict, so results serialise cleanly)."""

    def __init__(self, data=None, **kw):
        super().__init__(**kw)
        self.data = data


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def _safe_name(name):
    p = pathlib.PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError(f'unsafe cache name: {name!r}')
    return p


def _valid(b, validate):
    if not b:
        return False
    if validate is None:
        return True
    try:
        return bool(validate(b))
    except Exception:  # noqa: BLE001 - a validator that throws rejects the payload
        return False


def atomic_write(path, b):
    """Write bytes to path via a temporary file in the same directory and an atomic rename."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.part', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(b)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _space(url, min_interval, clock, sleep):
    if not min_interval:
        return
    host = urllib.parse.urlsplit(url).netloc
    with _lock:
        wait = _last.get(host, -1e18) + min_interval - clock()
        if wait > 0:
            sleep(wait)
        _last[host] = clock()


def get(url, source, name, *, validate=None, offline=False, refresh=False, root=None, opener=None, headers=None,
        retries=3, backoff=1.5, timeout=60, min_interval=0.0, sleep=time.sleep, clock=time.monotonic):
    """Bytes of `url`, cached at <root>/<source>/<name>. Returns a FetchResult (never raises on network failure)."""
    root = pathlib.Path(root) if root else RAW
    path = root / _safe_name(source) / _safe_name(name)
    cached = path.read_bytes() if path.is_file() else None
    cached_ok = cached is not None and _valid(cached, validate)
    base = {'url': url, 'path': str(path)}
    if cached_ok and not refresh:
        return FetchResult(cached, ok=True, status='cached', sha256=sha256(cached), bytes=len(cached), attempts=0, **base)
    if offline:
        if cached_ok:
            return FetchResult(cached, ok=True, status='stale_cache', sha256=sha256(cached), bytes=len(cached), attempts=0,
                               error='offline: refresh skipped', **base)
        return FetchResult(None, ok=False, status='offline_missing', sha256=None, bytes=0, attempts=0,
                           error='offline and no valid cached file', **base)
    opener = opener or urllib.request.urlopen
    hdr = {'User-Agent': UA, 'Accept': '*/*'}
    hdr.update(headers or {})
    err, attempts = None, 0
    for k in range(max(1, retries)):
        attempts = k + 1
        _space(url, min_interval, clock, sleep)
        try:
            with opener(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
                b = r.read()
            if _valid(b, validate):
                atomic_write(path, b)
                return FetchResult(b, ok=True, status='refreshed' if cached_ok else 'downloaded', sha256=sha256(b),
                                   bytes=len(b), attempts=attempts, **base)
            err = f'payload rejected by validator ({len(b or b"")} bytes)'
        except urllib.error.HTTPError as e:
            err = f'HTTP {e.code}'
            if e.code not in RETRY_HTTP:
                break
        except Exception as e:  # noqa: BLE001 - network errors, timeouts
            err = f'{type(e).__name__}: {e}'
        if k + 1 < retries:
            sleep(backoff * (2 ** k))
    if cached_ok:
        return FetchResult(cached, ok=True, status='stale_cache', sha256=sha256(cached), bytes=len(cached), attempts=attempts,
                           error=err, **base)
    return FetchResult(None, ok=False, status='failed', sha256=None, bytes=0, attempts=attempts, error=err, **base)


def file_record(path, root=None):
    """{'path': relative to the repo, 'sha256', 'bytes'} of a file on disk, for edition manifests."""
    p = pathlib.Path(path)
    b = p.read_bytes()
    try:
        rel = str(p.resolve().relative_to((pathlib.Path(root) if root else ROOT).resolve()))
    except ValueError:
        rel = str(p)
    return {'path': rel, 'sha256': sha256(b), 'bytes': len(b)}
