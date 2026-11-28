# DTCC parity corpus

One synthetic fixture decoded by both implementations of the DTCC rules: the lab's `harness/dtcc.py` and the
website's `api/cds.js`. It holds no market data: every record is written field by field to exercise one rule
(the `scenarios` map in `corpus.json` names them).

| File | What it is |
| --- | --- |
| `build.py` | Builds the corpus and runs this repository's decoder on it. `--check` fails if the files are stale. |
| `corpus.json` | 48 synthetic records in 5 daily files, every column the decoder reads. |
| `expected.json` | The lab decoder's output: daily level and diagnostics per name, and the lifecycle quality. |
| `SHA256SUMS` | Hashes of the two JSON files. |

**Checks.** Here, `tests/test_dtcc_parity_corpus.py` rebuilds the fixture, reproduces `expected.json` and pins each rule.
On the website, `tests/cds-parity-corpus.test.js` runs the JavaScript decoder on the same corpus and compares it with
`expected.json`, and the `cds-parity` workflow fails when its copy differs from this repository's main branch (on every
pull request, push and daily).

**Changing a rule.** Change both decoders together: rebuild here, copy `corpus.json`, `expected.json` and `SHA256SUMS`
to the website's `tests/fixtures/dtcc_parity/`, and update `api/cds.js` until its parity test passes. Merge the lab
first; the website's check compares against the lab's main branch.
