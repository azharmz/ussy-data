"""Read-only NPO evidence capture. Never repair prices or write R2."""
import hashlib
import io
import json
import logging
import math
import os
import platform
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

SID = 'US29355X1072'
DAY = '2026-09-01'
READY = 'production/ready/runs/46c00d08c7ec457f8df020278bc29bcb.parquet'
EXPECTED_SHA = '1abe6dcfe4b45e2ab9e23a803a3cfc1af5346a00e483aacc8aeaa81c294599e1'
FIELDS = ('open', 'high', 'low', 'close', 'adj_close', 'volume')


def now():
    return datetime.now(UTC).isoformat()


def differences(left, right):
    return {name: right[name] - left[name] for name in FIELDS
            if isinstance(left.get(name), (int, float)) and isinstance(right.get(name), (int, float))}


def qc(row):
    if any(not isinstance(row.get(k), (int, float)) or not math.isfinite(row[k]) for k in FIELDS):
        return ['nonfinite_or_missing']
    issues = []
    if row['open'] > row['high']: issues.append('open_above_high')
    if row['open'] < row['low']: issues.append('open_below_low')
    if row['close'] > row['high'] or row['close'] < row['low']: issues.append('close_outside_range')
    return issues


def read_evidence(s3, bucket, key, target):
    started = now()
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj['Body'].read()
    target.write_bytes(body)
    return body, {'key': key, 'read_started_at': started, 'read_finished_at': now(),
                  'etag': obj['ETag'], 'last_modified': str(obj.get('LastModified')),
                  'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}


def main():
    import pandas as pd
    import yfinance as yf
    from bootstrap_ohlcv import make_s3_client, normalize_history
    from update_production import extract_symbol
    out = Path('diagnostics/npo')
    out.mkdir(parents=True, exist_ok=False)
    report = {'started_at': now(), 'security_id': SID, 'ticker': 'NPO', 'date': DAY,
              'git_sha': os.getenv('GITHUB_SHA'), 'run_id': os.getenv('GITHUB_RUN_ID'),
              'python': platform.python_version(),
              'versions': {p: version(p) for p in ('yfinance', 'pandas', 'numpy', 'pyarrow', 'boto3')},
              'original_run_yfinance_version': 'unknown; retrieve original install logs',
              'limitations': ['Refetch is NEW evidence, not the original Yahoo response.',
                              'Raw yfinance frame is library output, not raw Yahoo HTTP JSON.',
                              'History/rolling/daily are current objects, potentially revised since original run.',
                              'One-ticker batch layout does not reproduce the original multi-ticker request.'],
              'objects': {}, 'stages': {}, 'fetches': [], 'errors': []}
    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    def sample(frame):
        selected = frame.copy()
        if 'security_id' in selected:
            selected = selected[selected['security_id'] == SID]
        selected = selected[pd.to_datetime(selected['date']).dt.strftime('%Y-%m-%d') == DAY]
        return json.loads(selected.to_json(orient='records', date_format='iso'))
    def stage(label, frame):
        rows = sample(frame)
        report['stages'][label] = {'rows': rows, 'qc': [qc(row) for row in rows]}
        save()
    save()
    # Silence library errors that might contain transport details. Record only exception types.
    logging.disable(logging.CRITICAL)
    s3, bucket = make_s3_client(), os.environ['R2_BUCKET_NAME']
    objects = {
        'reported_ready': READY,
        'history': f'backtest/ohlcv/{SID}.parquet',
        'rolling': 'production/rolling/latest.parquet',
        'daily': f'production/daily/{DAY}.parquet',
        'ready_pointer': 'production/ready/current.json',
        'readiness': 'production/rolling/readiness.json',
    }
    # Preserve stored evidence before making any Yahoo request; never call R2 mutation APIs.
    for label, key in objects.items():
        try:
            suffix = '.json' if key.endswith('.json') else '.parquet'
            body, metadata = read_evidence(s3, bucket, key, out / (label + suffix))
            report['objects'][label] = metadata
            if label == 'reported_ready':
                metadata['matches_reported_sha256'] = metadata['sha256'] == EXPECTED_SHA
            if suffix == '.parquet':
                stage(label, pd.read_parquet(io.BytesIO(body)))
        except Exception as exc:
            report['errors'].append({'stage': label, 'exception_type': type(exc).__name__})
        save()
    for label, metadata in report['objects'].items():
        try:
            metadata['unchanged_after_capture'] = s3.head_object(Bucket=bucket, Key=metadata['key'])['ETag'] == metadata['etag']
        except Exception as exc:
            metadata['stability_check_error_type'] = type(exc).__name__
    save()
    if any(label not in report['stages'] for label in ('reported_ready', 'history', 'rolling')):
        report['errors'].append({'stage': 'capture', 'reason': 'Required stored evidence unavailable; Yahoo refetch skipped'})
        save()
        return 1
    if not report['objects']['reported_ready']['matches_reported_sha256']:
        report['errors'].append({'stage': 'capture', 'reason': 'Ready hash differs from submitted QC; Yahoo refetch skipped'})
        save()
        return 1
    for layout in ('single', 'batch_layout'):
        if report['fetches']: time.sleep(3)
        params = dict(start='2026-08-25', end='2026-09-03', interval='1d',
                      auto_adjust=False, actions=False, progress=False, threads=False,
                      timeout=30, prepost=False, repair=False)
        if layout == 'batch_layout': params['group_by'] = 'ticker'
        fetch = {'layout': layout, 'parameters': params, 'tickers': ['NPO'], 'started_at': now(),
                 'evidence_kind': 'refetch_now_not_original_run'}
        report['fetches'].append(fetch)
        save()
        try:
            raw = yf.download('NPO' if layout == 'single' else ['NPO'], **params)
            fetch['finished_at'] = now()
            fetch['columns'] = [list(c) if isinstance(c, tuple) else c for c in raw.columns]
            raw.to_parquet(out / (layout + '_yfinance_raw.parquet'), index=True)
            raw.to_csv(out / (layout + '_yfinance_raw.csv'), index=True)
            if raw.empty: raise ValueError('Empty refetch')
            extracted = extract_symbol(raw, 'NPO', 1)
            extracted.to_csv(out / (layout + '_extracted.csv'), index=True)
            # Independent rename only, before repository normalization.
            before = extracted.reset_index().rename(columns={'Date': 'date', 'Datetime': 'date',
                'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close',
                'Adj Close': 'adj_close', 'Volume': 'volume'})
            stage(layout + '_before_normalization', before)
            normalized = normalize_history(extracted.copy(), SID, 'NPO')
            normalized.to_csv(out / (layout + '_normalized.csv'), index=False)
            stage(layout + '_normalized', normalized)
        except Exception as exc:
            fetch['finished_at'] = now()
            report['errors'].append({'stage': layout, 'exception_type': type(exc).__name__})
        save()
    baseline = report['stages']['reported_ready']['rows']
    report['deltas_vs_reported_ready'] = {}
    if len(baseline) == 1:
        for label, data in report['stages'].items():
            if len(data['rows']) == 1:
                report['deltas_vs_reported_ready'][label] = differences(baseline[0], data['rows'][0])
    report['normalization_deltas'] = {}
    for layout in ('single', 'batch_layout'):
        before = report['stages'].get(layout + '_before_normalization', {}).get('rows', [])
        after = report['stages'].get(layout + '_normalized', {}).get('rows', [])
        if len(before) == len(after) == 1:
            report['normalization_deltas'][layout] = differences(before[0], after[0])
    report['finished_at'] = now()
    save()
    print('NPO diagnosis captured. Read diagnostics/npo/report.json; no R2 writes or price repair performed.')
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('Diagnostic stopped:', type(exc).__name__, '(transport details suppressed)')
        raise SystemExit(1)
