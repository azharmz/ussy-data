"""Read-only full-universe reconciliation. No Yahoo calls and no R2 writes."""
import csv
import hashlib
import io
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from compliance import POLICY, is_eligible

REFERENCE = 'production/ready/runs/78808fe06fba4ae4b48162340ab066ca.parquet'
REFERENCE_SHA = 'ef49e66c4750b4b0ea47af233c08a033d31ca3356731f11951ac30764e98daa4'


def classify(row, peer_date, minimum):
    flags = []
    if row['history_status'] != 'available':
        flags.append(row['history_status'])
    else:
        if row['history_rows'] < minimum:
            flags.append('insufficient_history')
        if peer_date and row['history_last_date'] < peer_date:
            flags.append('behind_exchange_peers_calendar_unverified')
        if row.get('rolling_last_date') != row['history_last_date']:
            flags.append('history_rolling_date_mismatch')
    if row.get('in_ready') and row.get('ready_last_date') != row.get('rolling_last_date'):
        flags.append('rolling_ready_date_mismatch')
    if row.get('in_ready') != row.get('declared_ready'):
        flags.append('readiness_export_membership_mismatch')
    if row.get('history_date_problems'):
        flags.append('invalid_or_duplicate_history_dates')
    return flags


def main():
    import pandas as pd
    from bootstrap_ohlcv import make_s3_client
    out = Path('diagnostics/universe-audit')
    out.mkdir(parents=True, exist_ok=False)
    report = {'started_at': datetime.now(UTC).isoformat(), 'status': 'running',
              'eligibility_policy': POLICY, 'yahoo_downloads': 0, 'r2_writes': 0,
              'objects': {}, 'errors': [], 'source_changes': [],
              'limitations': ['Peer dates are a screening signal, NOT verified missing trading sessions.',
                             'No exchange calendars or trading-halt feeds queried.',
                             'Ready means history eligibility, not independently verified market freshness.']}
    rows = []
    def save():
        text = json.dumps(report, indent=2, default=str)
        for name in ('R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_ENDPOINT'):
            value = os.getenv(name)
            if value:
                text = text.replace(value, '[REDACTED]')
        (out / 'report.json').write_text(text, encoding='utf-8')
        if rows:
            fields = sorted(set().union(*(r.keys() for r in rows)))
            with (out / 'securities.csv').open('w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
    save()
    try:
        s3, bucket = make_s3_client(), os.environ['R2_BUCKET_NAME']
        def read(key):
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj['Body'].read()
            report['objects'][key] = {'etag': obj['ETag'], 'bytes': len(body),
                'last_modified': str(obj.get('LastModified')), 'sha256': hashlib.sha256(body).hexdigest()}
            return body
        def document(key):
            return json.loads(read(key))
        def groups(frame):
            return {str(sid): group for sid, group in frame.groupby('security_id')}
        def dates(frame):
            return pd.to_datetime(frame['date'], errors='coerce', utc=True)
        def last(frame):
            value = dates(frame).max()
            return None if pd.isna(value) else value.date().isoformat()
        current = document('universe/current.json')
        snapshot = current['snapshot_date']
        membership = document(f'universe/membership/{snapshot}.json')
        members = [r for r in membership['records'] if is_eligible(r)]
        if len({r['security_id'] for r in members}) != len(members):
            raise ValueError('Duplicate eligible membership IDs')
        readiness = document('production/rolling/readiness.json')
        pointer = document('production/ready/current.json')
        report.update(snapshot_date=snapshot, confirmed_compliant=len(members),
                      readiness_snapshot=readiness.get('snapshot_date'),
                      ready_snapshot=pointer.get('snapshot_date'),
                      ready_key=pointer['parquet_key'], ready_created_at=pointer.get('created_at'))
        payload = read(pointer['parquet_key'])
        if hashlib.sha256(payload).hexdigest() != pointer['sha256']:
            raise ValueError('Ready SHA mismatch')
        ready = groups(pd.read_parquet(io.BytesIO(payload)))
        rolling = groups(pd.read_parquet(io.BytesIO(read('production/rolling/latest.parquet'))))
        reference = {}
        try:
            ref = read(REFERENCE)
            if hashlib.sha256(ref).hexdigest() != REFERENCE_SHA:
                raise ValueError('Reference SHA mismatch')
            reference = groups(pd.read_parquet(io.BytesIO(ref), columns=['security_id', 'date']))
            report['reference_verified'] = True
        except Exception as exc:
            report['errors'].append({'stage': 'reference', 'type': type(exc).__name__})
            report['reference_verified'] = False
        listing = {}
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix='backtest/ohlcv/'):
            for obj in page.get('Contents', []):
                listing[obj['Key']] = obj['ETag']
        declared = set(readiness.get('ready_security_ids', []))
        minimum = readiness['minimum_ready_bars']
        for index, member in enumerate(sorted(members, key=lambda r: r['security_id'])):
            sid = str(member['security_id'])
            key = f'backtest/ohlcv/{sid}.parquet'
            row = dict(security_id=sid, ticker=member.get('ticker'), exchange=member.get('exchange'),
                sharia_compliance=member.get('sharia_compliance'),
                secondary_rating_audit_only=member.get('musaffaHalalRating'),
                excluded_by_old_dual_filter=member.get('musaffaHalalRating') != 'COMPLIANT',
                history_status='missing_history', history_rows=0, history_last_date=None,
                in_ready=sid in ready, declared_ready=sid in declared,
                rolling_rows=len(rolling[sid]) if sid in rolling else 0,
                rolling_last_date=last(rolling[sid]) if sid in rolling else None,
                ready_rows=len(ready[sid]) if sid in ready else 0,
                ready_last_date=last(ready[sid]) if sid in ready else None,
                reference_ready_last_date=last(reference[sid]) if sid in reference else None)
            if key in listing:
                try:
                    frame = pd.read_parquet(io.BytesIO(read(key)), columns=['security_id', 'date'])
                    if set(frame['security_id'].astype(str)) != {sid}:
                        raise ValueError('Historical identity mismatch')
                    ds = dates(frame)
                    if frame.empty or ds.dropna().empty:
                        raise ValueError('Empty or invalid history')
                    row.update(history_status='available', history_rows=len(frame), history_last_date=last(frame),
                        history_first_date=ds.min().date().isoformat(),
                        history_date_problems=int(ds.isna().sum() + ds.duplicated().sum()))
                    if sid in rolling:
                        row['rolling_dates_absent_from_history'] = len(set(dates(rolling[sid])) - set(ds))
                    if report['objects'][key]['etag'] != listing[key]:
                        report['source_changes'].append(key)
                except Exception as exc:
                    row.update(history_status='history_read_error', history_error_type=type(exc).__name__)
            rows.append(row)
            if (index + 1) % 100 == 0:
                print(f'Inspected {index + 1}/{len(members)} histories (R2 only)', flush=True)
                save()
        peers = {}
        for row in rows:
            if row['exchange'] and row['history_last_date']:
                peers[row['exchange']] = max(peers.get(row['exchange'], ''), row['history_last_date'])
        for row in rows:
            row['exchange_peer_latest'] = peers.get(row['exchange'])
            row['flags'] = '|'.join(classify(row, row['exchange_peer_latest'], minimum))
            if row.get('rolling_dates_absent_from_history'):
                row['flags'] += '|rolling_dates_absent_from_history'
            if row['reference_ready_last_date'] and row['ready_last_date']:
                row['advanced_since_reference'] = row['ready_last_date'] > row['reference_ready_last_date']
        # Recent manifests provide recorded failures, not inferred causes.
        manifest_keys = []
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix='production/manifests/'):
            manifest_keys.extend(page.get('Contents', []))
        report['recent_production_manifests'] = []
        for obj in sorted(manifest_keys, key=lambda x: x['LastModified'], reverse=True)[:10]:
            try:
                manifest = document(obj['Key'])
                report['recent_production_manifests'].append({'key': obj['Key'],
                    'created_at': manifest.get('created_at'), 'failures': manifest.get('failures', []),
                    'operational_securities': manifest.get('operational_securities')})
            except Exception as exc:
                report['errors'].append({'stage': 'manifest', 'type': type(exc).__name__})
        for key, meta in list(report['objects'].items()):
            try:
                if s3.head_object(Bucket=bucket, Key=key)['ETag'] != meta['etag']:
                    report['source_changes'].append(key)
            except Exception as exc:
                report['source_changes'].append(key)
        eligible_ids = {r['security_id'] for r in rows}
        ending_listing = {}
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix='backtest/ohlcv/'):
            for obj in page.get('Contents', []):
                ending_listing[obj['Key']] = obj['ETag']
        for sid in eligible_ids:
            key = f'backtest/ohlcv/{sid}.parquet'
            if listing.get(key) != ending_listing.get(key):
                report['source_changes'].append(key)
        report['reconciliation'] = {
            'ready_not_eligible': sorted(set(ready) - eligible_ids),
            'rolling_not_eligible': sorted(set(rolling) - eligible_ids),
            'readiness_count_matches_membership': readiness.get('confirmed_compliant') == len(members),
            'ready_manifest_count_matches': pointer.get('securities') == len(ready),
            'snapshot_matches': readiness.get('snapshot_date') == snapshot == pointer.get('snapshot_date')}
        report['summary'] = {'compliant': len(rows), 'ready': len(ready),
            'history_status': dict(Counter(r['history_status'] for r in rows)),
            'flags': dict(Counter(flag for r in rows for flag in r['flags'].split('|') if flag)),
            'excluded_by_old_dual_filter': sum(r['excluded_by_old_dual_filter'] for r in rows)}
        report['review_candidates'] = [r for r in rows if r['flags']]
        report['five_previously_reported'] = [r for r in rows if r['ticker'] in ('ABUS','AUPH','BTE','BRLS','CLS')]
        report['status'] = 'complete' if not report['source_changes'] and not report['errors'] and not any(r['history_status'] == 'history_read_error' for r in rows) else 'incomplete_or_sources_changed'
        report['finished_at'] = datetime.now(UTC).isoformat()
        save()
        print(json.dumps(report['summary'], indent=2))
        print('Audit findings are not a Yahoo download instruction. See report.json and securities.csv.')
        return 0 if report['status'] == 'complete' else 1
    except Exception as exc:
        report.update(status='incomplete', error_type=type(exc).__name__)
        save()
        print('Audit stopped. Inspect artifact; no R2 writes performed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
