"""Isolated SPY benchmark: immutable versions + conditional pointer publication."""
import hashlib
import io
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from ohlcv_qc import validate_frame

POINTER = 'benchmarks/SPY/current.json'
PREFIX = 'benchmarks/SPY/runs/'
IDENTITY = 'benchmark:SPY'
COLS = ['date', 'security_id', 'ticker', 'open', 'high', 'low', 'close', 'adj_close', 'volume']
REQUIRED_SOURCE = ['date', 'open', 'high', 'low', 'close']


def validate(frame):
    import pandas as pd
    if set(COLS) - set(frame.columns) or frame.empty:
        raise ValueError('SPY schema missing or empty')
    if set(frame['ticker']) != {'SPY'} or set(frame['security_id']) != {IDENTITY}:
        raise ValueError('SPY identity mismatch')
    dates = pd.to_datetime(frame['date'], errors='coerce')
    if dates.isna().any() or dates.duplicated().any():
        raise ValueError('Invalid or duplicate dates')
    validate_frame(frame)


def merge_incremental(old, new):
    import numpy as np
    import pandas as pd
    validate(old)
    validate(new)
    overlap = old.merge(new, on='date', suffixes=('_old', '_new'))
    if overlap.empty:
        raise ValueError('No overlap to verify adjustment continuity')
    # Dividend/split revisions can affect history outside the lookback window.
    # Do not silently stitch incompatible adjusted series or redownload everything.
    if not np.allclose(overlap.adj_close_old / overlap.close_old,
                       overlap.adj_close_new / overlap.close_new, rtol=1e-7, atol=1e-9):
        raise ValueError('Adjustment basis changed; explicit historical revision review required')
    if not np.allclose(overlap.close_old, overlap.close_new, rtol=1e-7, atol=1e-9):
        raise ValueError('Source revised historical closes; review before replacing history')
    result = pd.concat([old, new], ignore_index=True).drop_duplicates('date', keep='last')
    result = result[COLS].sort_values('date').reset_index(drop=True)
    validate(result)
    return result


def audit_and_normalize_download(raw, out):
    """Normalize Yahoo rows while making any discarded incomplete rows explicit.

    Yahoo can occasionally include a placeholder/incomplete daily row. Such a row is
    not a usable market bar and may be discarded, but only after it is recorded.
    Duplicate valid dates or unexplained row loss still stop publication.
    """
    import pandas as pd
    from bootstrap_ohlcv import normalize_history

    flat = raw.copy()
    if isinstance(flat.columns, pd.MultiIndex):
        if 'SPY' not in flat.columns.get_level_values(1):
            raise ValueError('Unexpected Yahoo column layout')
        flat = flat.xs('SPY', axis=1, level=1)
    source = flat.reset_index().rename(columns={
        'Date': 'date', 'Datetime': 'date', 'Open': 'open', 'High': 'high',
        'Low': 'low', 'Close': 'close', 'Adj Close': 'adj_close', 'Volume': 'volume',
    })
    missing = set(REQUIRED_SOURCE) - set(source.columns)
    if missing:
        raise ValueError(f'Missing Yahoo SPY columns: {sorted(missing)}')

    source['date'] = pd.to_datetime(source['date'], errors='coerce', utc=True)
    numeric = source.copy()
    for column in ['open', 'high', 'low', 'close']:
        numeric[column] = pd.to_numeric(numeric[column], errors='coerce')
    valid_mask = numeric[REQUIRED_SOURCE].notna().all(axis=1)
    dropped = source.loc[~valid_mask].copy()
    if not dropped.empty:
        dropped.to_csv(out / 'discarded-incomplete-source-rows.csv', index=False)
    valid_source = source.loc[valid_mask].copy()
    if valid_source['date'].duplicated().any():
        raise ValueError('Yahoo returned duplicate valid SPY dates')

    new = normalize_history(flat, IDENTITY, 'SPY')
    if len(new) != len(valid_source):
        raise ValueError(
            f'Normalization changed valid-row count: source_valid={len(valid_source)} normalized={len(new)}'
        )
    return new, int(len(dropped)), int(len(valid_source))


def main():
    import pandas as pd
    import yfinance as yf
    from bootstrap_ohlcv import make_s3_client
    out = Path('diagnostics/spy')
    out.mkdir(parents=True, exist_ok=False)
    report = {'started_at': datetime.now(UTC).isoformat(), 'status': 'checking_existing',
              'ticker': 'SPY', 'pointer_key': POINTER, 'git_sha': os.getenv('GITHUB_SHA'),
              'versions': {p: version(p) for p in ('yfinance', 'pandas', 'pyarrow', 'boto3')}}
    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    save()
    try:
        logging.disable(logging.CRITICAL)
        s3, bucket = make_s3_client(), os.environ['R2_BUCKET_NAME']
        def get(key):
            obj = s3.get_object(Bucket=bucket, Key=key)
            return obj['Body'].read(), obj['ETag']
        pointer, pointer_etag, old = None, None, None
        try:
            pointer_raw, pointer_etag = get(POINTER)
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') not in ('NoSuchKey', '404', 'NotFound'):
                raise
        else:
            pointer = json.loads(pointer_raw)
            if pointer.get('ticker') != 'SPY' or not pointer.get('parquet_key', '').startswith(PREFIX):
                raise ValueError('Unrecognized SPY pointer; review existing data')
            payload, _ = get(pointer['parquet_key'])
            if hashlib.sha256(payload).hexdigest() != pointer['sha256']:
                raise ValueError('Existing SPY hash mismatch')
            (out / 'previous-pointer.json').write_bytes(pointer_raw)
            old = pd.read_parquet(io.BytesIO(payload))
            validate(old)
            if pointer.get('adjustment_policy') != 'yahoo_auto_adjust_false_adj_close_separate_v1':
                raise ValueError('Unknown existing adjustment basis')
        if old is None:
            candidates = []
            # Inventory only: never read/download unrelated histories.
            for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket):
                for item in page.get('Contents', []):
                    key = item['Key']
                    parts = key.upper().replace('.', '/').replace('_', '/').replace('-', '/').split('/')
                    if ('SPY' in parts or 'US78462F1030' in key.upper()) and key.lower().endswith(('.parquet', '.json')):
                        candidates.append(key)
            report['existing_candidates'] = candidates
            if candidates:
                raise ValueError('Possible existing SPY data found without official pointer; review before download')
            if os.getenv('GITHUB_EVENT_NAME') == 'schedule':
                report['status'] = 'awaiting_manual_initialization'
                save()
                print('SPY not initialized. Run this workflow manually once.')
                return 0
        now = datetime.now(UTC)
        # Conservative window: exclude today's US bar before 22:00 UTC.
        end = now.date() + (timedelta(days=1) if now.hour >= 22 else timedelta())
        params = dict(start='1990-01-01' if old is None else (pd.to_datetime(old.date).max().date() - timedelta(days=10)).isoformat(),
                      end=end.isoformat(), interval='1d', auto_adjust=False, actions=False,
                      repair=False, prepost=False, progress=False, threads=False, timeout=30)
        report.update(status='fetching', mode='bootstrap' if old is None else 'incremental', parameters=params)
        save()
        raw = yf.download('SPY', **params)
        raw.to_parquet(out / 'source-refetch.parquet')
        if raw.empty:
            raise ValueError('Source empty')
        new, discarded_rows, source_valid_rows = audit_and_normalize_download(raw, out)
        report.update(source_rows=int(len(raw)), source_valid_rows=source_valid_rows,
                      discarded_incomplete_source_rows=discarded_rows)
        save()
        result = new if old is None else merge_incremental(old, new)
        if old is not None and result.equals(old[COLS].sort_values('date').reset_index(drop=True)):
            report.update(status='unchanged', rows=len(old), last_date=str(old.date.max()))
            save()
            print('SPY unchanged; existing pointer retained.')
            return 0
        buffer = io.BytesIO()
        result.to_parquet(buffer, index=False, compression='zstd')
        body = buffer.getvalue()
        key = PREFIX + uuid4().hex + '.parquet'
        manifest = dict(schema_version=1, ticker='SPY', security_id=IDENTITY, role='market_benchmark_only',
            created_at=datetime.now(UTC).isoformat(), parquet_key=key, sha256=hashlib.sha256(body).hexdigest(),
            rows=len(result), first_date=result.date.min().date().isoformat(), last_date=result.date.max().date().isoformat(),
            columns=COLS, adjustment_policy='yahoo_auto_adjust_false_adj_close_separate_v1',
            adjustment_note='OHLC as provided by Yahoo with auto_adjust=False; not manually adjusted. Adj Close separate. Not a guarantee of split-unadjusted historical prices.',
            source='yfinance/Yahoo Finance', fetch_parameters=params, versions=report['versions'],
            discarded_incomplete_source_rows=discarded_rows,
            previous_parquet_key=pointer['parquet_key'] if pointer else None,
            previous_pointer=pointer, qc='passed', currency='USD')
        manifest['dtypes'] = {c: str(result[c].dtype) for c in COLS}
        report.update(status='publishing', manifest=manifest)
        save()
        s3.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch='*', ContentType='application/vnd.apache.parquet')
        check, _ = get(key)
        if hashlib.sha256(check).hexdigest() != manifest['sha256']:
            raise ValueError('Published Parquet failed verification; pointer unchanged')
        # Immutable run manifest keeps audit metadata without recursive pointer nesting.
        manifest.pop('previous_pointer')
        meta_key = key.removesuffix('.parquet') + '.json'
        manifest['run_manifest_key'] = meta_key
        meta = json.dumps(manifest, indent=2).encode()
        s3.put_object(Bucket=bucket, Key=meta_key, Body=meta, IfNoneMatch='*', ContentType='application/json')
        condition = {'IfMatch': pointer_etag} if pointer_etag else {'IfNoneMatch': '*'}
        s3.put_object(Bucket=bucket, Key=POINTER, Body=meta, ContentType='application/json', **condition)
        report.update(status='complete', finished_at=datetime.now(UTC).isoformat())
        save()
        print(json.dumps(manifest, indent=2))
        return 0
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__)
        if isinstance(exc, ValueError):
            message = str(exc)
            for name in ('R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_ENDPOINT'):
                if os.getenv(name): message = message.replace(os.environ[name], '[REDACTED]')
            report['validation_error'] = message
        save()
        print('SPY stopped. Inspect artifact; no universe or stock history changed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
