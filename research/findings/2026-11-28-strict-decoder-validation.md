# Strict CDS decoder: bounded replay and adversarial checks

The current CoreWeave CDS reading cannot be selected from an unsigned upfront alone. The strict decoder withholds an upfront when both payment directions produce valid model roots. An eligible quote, a previous value, or a coupon-based spread floor cannot establish that trade's payment direction.

This is a correction to the pricing logic, not the adoption of new index weights or a claim of settlement readiness. The simplified flat-hazard model still needs independent economic validation.

## Reproduction

```bash
python research/tools/validate_strict_decoder.py \
  --raw-dir /private/dtcc \
  --site-dir /checkout/Ai-credit-stress-index- \
  --output-dir /private/validation \
  --baseline-ref eab38c87947684cc428a941522ad8bba00e8c9b4
```

The runner reads existing date-named ZIPs only. It verifies every file against `manifests/dtcc_sha256.csv`; a missing manifest entry or mismatched hash stops the run. No source records are published by the runner. It writes aggregate results and derived entity-day diagnostics to the supplied output directory.

## Scope and observed result

The available sample contains 42 hash-verified daily files from 1 October through 27 November 2026. It is a bounded retrospective replay, not a complete historical ledger or a point-in-time backtest. Reports in the window can refer to ancestors outside it, and later corrections may be absent.

| Entity | Accepted days, prior decoder | Accepted days, strict decoder | Accepted trades, strict decoder | Ambiguous upfronts excluded |
| --- | ---: | ---: | ---: | ---: |
| Oracle | 37 | 37 | 327 | 0 |
| CoreWeave | 31 | 0 | 0 | 87 |
| Meta | 33 | 22 | 46 | 153 |

The latest accepted Oracle level remains 373.2 bp on 25 November, from 11 upfront-implied trades. On 24 November the one eligible CoreWeave upfront admits approximately **12.1 bp and 1,372.7 bp** under the simplified model. The data alone does not choose between them. Neither value is published as a strict CoreWeave level.

Python and JavaScript agree on all 59 accepted entity-days and all 126 entity-day quality records. This is implementation parity; it is not an independent validation of the pricing model.

The bounded lifecycle ledger reports 60 orphan lineages, five cases of ambiguous correction ordering, one cyclic lineage and two unknown-action records. There are 63 quarantined lineages; these categories overlap. The missing ancestors and ambiguous lifecycle records make the source quality partial. A reconstructed research level must not be represented as a complete fixing.

## Adversarial checks

The same offline runner checks both implementations against five synthetic scenarios:

- An eligible injected quote cannot choose the direction of an ambiguous upfront.
- A valid 4,000 bp quote survives; an empirical ceiling does not hide a large repricing.
- An out-of-order three-report correction chain creates one effective trade.
- A cancellation of the last correction removes its lineage.
- Missing required columns and a missing requested source file fail closed.

One eligible standalone quote can still set a **research** level. These checks do not establish the cost or feasibility of market manipulation, independence of reporting counterparties, or protection against coordinated trades. Publication and settlement need additional policy and validation gates; trade count alone is insufficient.

## What would change the conclusion

A documented source field or independently verified trade-specific record identifying the upfront payer would resolve payment direction without guessing. A validated signed quotation feed could supply an independently defined price observation. A licence for the same unsigned records, by itself, does not supply the missing direction.

The raw window, code hashes, aggregate diagnostics and baseline commit are recorded in the private validation output. Full-history scorecards and the lab's forward test must be rerun separately before claims of improved market information or institutional readiness.
