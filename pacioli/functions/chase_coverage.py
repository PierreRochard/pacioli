"""Shared inclusive-date coverage for planning exports and reconciling balances."""
from datetime import timedelta, timezone

from pacioli.extensions import db
from pacioli.models import OFXFileImports, ChaseExportCoverage


def merge_ranges(intervals):
    merged = []
    for begin, end in sorted(intervals):
        if begin is None or end is None or begin > end:
            raise ValueError('Invalid coverage')
        if merged and begin <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    return merged


def account_ranges(account_id):
    """Prefer verified request bounds; retain valid manual QFX bounds otherwise.

    An invalid unverified import remains an explicit blocker. A verified export
    for that same file can supply the missing bounds without modifying source.
    """
    imports = db.session.query(OFXFileImports).filter_by(ofx_account_id=account_id).all()
    verified = {}
    for row in (db.session.query(ChaseExportCoverage)
                .join(OFXFileImports, OFXFileImports.id == ChaseExportCoverage.file_import_id)
                .filter(OFXFileImports.ofx_account_id == account_id)):
        verified.setdefault(row.file_import_id, []).append((row.start, row.end))
    intervals, invalid = [], False
    for source in imports:
        if source.id in verified:
            intervals.extend(verified[source.id])
        elif (source.coverage_start and source.coverage_end and
              source.coverage_start <= source.coverage_end):
            intervals.append((source.coverage_start.astimezone(timezone.utc).date(),
                              source.coverage_end.astimezone(timezone.utc).date()))
        else:
            invalid = True
    return merge_ranges(intervals), invalid


def missing_ranges(intervals, start, end):
    """Return uncovered dates in a bounded reconciliation interval."""
    cursor, missing = start, []
    for left, right in intervals:
        if right < cursor:
            continue
        if left > end:
            break
        if left > cursor:
            missing.append((cursor, min(end, left - timedelta(days=1))))
        cursor = max(cursor, right + timedelta(days=1))
        if cursor > end:
            break
    if cursor <= end:
        missing.append((cursor, end))
    return missing
