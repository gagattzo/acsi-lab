"""Public bond-value boundary; diagnostic metadata is not a data licence."""
from datetime import date
import re

MARK_FIELDS = frozenset({
    'g_spread_equal_weighted_bp', 'g_spread_issuer_capped_bp', 'spread_bp',
    'median_price', 'median_yield', 'latest_median_price', 'latest_median_yield',
    'latest_g_spread_bp', 'reported_volume_lower_bound', 'total_volume_today',
    'lastSalePrice', 'lastSaleYield', 'reportedTradeVolume',
})


def public_market_data_authorized(manifest, *days):
    """Check the exporter-attested rights scope, including carried source dates.

    This is a software boundary, not independent verification of a contract.
    The exporter creates these fields only from a separate reviewed rights record.
    """
    try:
        if (manifest.get('private_candidate') or
                manifest.get('public_market_data_authorized') is not True or
                manifest.get('collection_verification_status') != 'verified_snapshot' or
                manifest.get('publication_authorization_status') != 'recorded_authorization' or
                manifest.get('publication_blockers') or
                not re.fullmatch(r'[0-9a-f]{64}', manifest.get('publication_authorization_sha256') or '')):
            return False
        start = date.fromisoformat(manifest['publication_authorization_valid_from'])
        end = date.fromisoformat(manifest['publication_authorization_valid_through'])
        return bool(days) and all(start <= date.fromisoformat(day) <= end for day in days)
    except (KeyError, TypeError, ValueError):
        return False


def withhold_market_values(row):
    result = dict(row)
    for key in MARK_FIELDS.intersection(result):
        result[key] = None
    result.update(publication_eligible=False, market_data_withheld=True)
    return result
