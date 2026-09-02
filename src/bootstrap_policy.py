"""Download the reviewed policy-migration queue; never overwrite histories."""
import io
import json
import logging
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from compliance import is_eligible


def select_candidates(plan, membership, snapshot):
    if plan['snapshot_date'] != snapshot or membership.get('snapshot_date') != snapshot:
        raise ValueError('Snapshot changed: review a new plan first')
    records = membership['records']
    if membership.get('count') != len(records):
        raise ValueError('Invalid membership count')
    by_id = {str(row['security_id']): row for row in records}
    if len(by_id) != len(records):
        raise ValueError('Duplicate membership IDs')
    candidates = plan['candidates']
    ids = [str(row['security_id']) for row in candidates]
    deferred = {str(row['security_id']) for row in plan['deferred']}
    if len(ids) != len(set(ids)) or set(ids) & deferred:
        raise ValueError('Duplicate or overlapping queue IDs')
    selected, excluded = [], []
    for row in candidates:
        current = by_id.get(str(row['security_id']))
        if current is None or not is_eligible(current):
            excluded.append(row)
        else:
            selected.append(current)
    return selected, excluded


def main():
    from bootstrap_ohlcv import make_s3_client, object_exists, download_history, normalize_history, yahoo_symbol
    plan = json.loads((Path(__file__).resolve().parents[1] / 'config/bootstrap-policy-2026-08-28.json').read_text())
    s3, bucket = make_s3_client(), os.environ['R2_BUCKET_NAME']
    def read(key):
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj['Body'].read()), obj['ETag']
    current, current_etag = read('universe/current.json')
    snapshot = current['snapshot_date']
    member_key = f'universe/membership/{snapshot}.json'
    membership, member_etag = read(member_key)
    selected, excluded = select_candidates(plan, membership, snapshot)
    report_key = f'backtest/manifests/bootstrap/{snapshot}/policy-bootstrap-{os.getenv("GITHUB_RUN_ID", "local")}-{os.getenv("GITHUB_RUN_ATTEMPT", "1")}.json'
    results = []
    report = {'snapshot_date': snapshot, 'source_audit': plan['source_audit'],
              'deferred_reason': 'Prior unresolved cases; individual review required, NOT a compliance exclusion',
              'deferred': plan['deferred'], 'excluded_by_current_membership': excluded,
              'planned': len(selected), 'results': results}
    def save():
        report['created_at'] = datetime.now(UTC).isoformat()
        report['summary'] = dict(Counter(row['status'] for row in results))
        report['unprocessed'] = len(selected) - len(results)
        s3.put_object(Bucket=bucket, Key=report_key, Body=json.dumps(report, indent=2).encode(), ContentType='application/json')
    save()
    failures = 0
    attempted = False
    for position, row in enumerate(selected, 1):
        sid, ticker = str(row['security_id']), row['ticker']
        key = f'backtest/ohlcv/{sid}.parquet'
        for source, etag in [('universe/current.json', current_etag), (member_key, member_etag)]:
            if s3.head_object(Bucket=bucket, Key=source)['ETag'] != etag:
                save()
                raise RuntimeError('Universe changed during bootstrap; stop and review')
        symbol = yahoo_symbol(ticker, sid)
        result = {'security_id': sid, 'ticker': ticker, 'yahoo_ticker': symbol, 'object_key': key}
        if object_exists(s3, bucket, key):
            result['status'] = 'existing'
        else:
            if attempted:
                time.sleep(5)
            attempted = True
            logging.info('Downloading %s/%s: %s (Yahoo: %s)', position, len(selected), ticker, symbol)
            try:
                frame = normalize_history(download_history(symbol, 1), sid, ticker)
                if frame.empty:
                    raise ValueError('No normalized rows')
                buf = io.BytesIO()
                frame.to_parquet(buf, engine='pyarrow', index=False, compression='zstd')
                body = buf.getvalue()
            except Exception as exc:
                failures += 1
                result.update(status='failed', error=str(exc)[:500])
                results.append(result)
                save()
                print(f'::warning::Download failed for {ticker}; see R2 manifest', flush=True)
                if failures >= 3 or 'ratelimit' in str(exc).lower() or 'too many requests' in str(exc).lower():
                    report['stopped_early'] = True
                    save()
                    break
                continue
            # Conditional creation protects even against another writer after HEAD.
            try:
                s3.put_object(Bucket=bucket, Key=key, Body=body,
                              ContentType='application/vnd.apache.parquet', IfNoneMatch='*')
                if s3.head_object(Bucket=bucket, Key=key)['ContentLength'] != len(body):
                    raise RuntimeError('Uploaded size verification failed')
                result.update(status='uploaded', rows=len(frame))
            except Exception as exc:
                if getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('PreconditionFailed', '412'):
                    result['status'] = 'existing'
                else:
                    raise
            failures = 0
        results.append(result)
        save()
    save()
    print(json.dumps({k: v for k, v in report.items() if k not in ('results', 'deferred')}, indent=2))
    print('Bootstrap report: ' + report_key)
    if report['unprocessed'] or report['summary'].get('failed'):
        print('::warning::Some histories remain unavailable/unprocessed; inspect report before retry')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    main()
