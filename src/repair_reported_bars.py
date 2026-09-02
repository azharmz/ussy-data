"""Repair only the 34 reported security/date pairs, preserving original evidence."""
import csv
import hashlib
import io
import json
import logging
import os
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from ohlcv_qc import FIELDS, issues, validate_frame

DAY = '2026-09-01'


def replace_bad_rows(frame, targets, replacements):
    """Never add/delete rows, touch other dates, or overwrite already-valid bars."""
    import pandas as pd
    result = frame.copy()
    changed = []
    for target in targets:
        sid = target['security_id']
        mask = (result['security_id'] == sid) & (pd.to_datetime(result['date']).dt.strftime('%Y-%m-%d') == DAY)
        if int(mask.sum()) != 1:
            raise ValueError(f'Expected exactly one target row: {sid}')
        old = result.loc[mask].iloc[0].to_dict()
        if not issues(old):
            continue
        # Do not replace a different, newly discovered corruption automatically.
        if any(float(old[key]) != float(target[key]) for key in FIELDS):
            raise ValueError(f'Invalid bar differs from submitted evidence: {sid}')
        new = replacements[sid]
        if issues(new):
            raise ValueError(f'Invalid replacement: {sid}')
        for key in FIELDS:
            result.loc[mask, key] = new[key]
        changed.append(sid)
    return result, changed


def main():
    import pandas as pd
    import yfinance as yf
    from bootstrap_ohlcv import make_s3_client, normalize_history, yahoo_symbol
    from update_production import extract_symbol
    from export_ready import main as export_ready

    out = Path('diagnostics/repair-bars')
    out.mkdir(parents=True, exist_ok=False)
    report = {'started_at': datetime.now(UTC).isoformat(), 'status': 'capturing',
              'git_sha': os.getenv('GITHUB_SHA'), 'versions': {p: version(p) for p in ('yfinance', 'pandas', 'numpy', 'boto3')},
              'objects': {}, 'fetches': [], 'changes': {}, 'writes': [],
              'evidence_note': 'Refetch now, not the original Yahoo response; no synthetic price correction.'}
    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    save()
    try:
        logging.disable(logging.CRITICAL)
        with (Path(__file__).resolve().parents[1] / 'audits/2026-09-01-invalid-bars.csv').open(encoding='utf-8') as stream:
            targets = list(csv.DictReader(stream))
        if len(targets) != 34 or len({r['security_id'] for r in targets}) != 34 or any(r['date'] != DAY for r in targets):
            raise ValueError('Unexpected repair scope')
        s3, bucket = make_s3_client(), os.environ['R2_BUCKET_NAME']
        prefix = f'audit/ohlcv-repairs/{uuid4().hex}'
        report['backup_prefix'] = prefix
        captured = {}
        def capture(key):
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj['Body'].read()
            captured[key] = (body, obj['ETag'])
            target = out / 'before' / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            report['objects'][key] = {'etag': obj['ETag'], 'last_modified': str(obj.get('LastModified')),
                'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
            save()
        for key in ('universe/current.json', 'production/ready/current.json', 'production/rolling/readiness.json',
                    'production/rolling/latest.parquet', f'production/daily/{DAY}.parquet'):
            capture(key)
        pointer = json.loads(captured['production/ready/current.json'][0])
        capture(pointer['parquet_key'])
        if hashlib.sha256(captured[pointer['parquet_key']][0]).hexdigest() != pointer['sha256']:
            raise ValueError('Current ready hash mismatch')
        for row in targets:
            capture(f"backtest/ohlcv/{row['security_id']}.parquet")
        # Verified durable backups of ALL originals before downloads or repairs.
        for key, (body, _) in captured.items():
            backup = prefix + '/before/' + key
            s3.put_object(Bucket=bucket, Key=backup, Body=body, IfNoneMatch='*')
            actual = s3.get_object(Bucket=bucket, Key=backup)['Body'].read()
            if hashlib.sha256(actual).digest() != hashlib.sha256(body).digest():
                raise RuntimeError('Backup verification failed')
        replacements = {}
        report['status'] = 'refetching'
        params = dict(start=DAY, end='2026-09-02', interval='1d', auto_adjust=False, actions=False,
                      progress=False, threads=False, timeout=30, prepost=False, repair=False)
        for index, row in enumerate(targets):
            if index:
                time.sleep(3)
            sid, ticker = row['security_id'], row['ticker']
            symbol = yahoo_symbol(ticker, sid)
            fetch = {'security_id': sid, 'ticker': ticker, 'symbol': symbol, 'parameters': params,
                     'started_at': datetime.now(UTC).isoformat()}
            report['fetches'].append(fetch)
            print(f'Checking {index + 1}/34: {ticker}', flush=True)
            save()
            raw = yf.download(symbol, **params)
            fetch['finished_at'] = datetime.now(UTC).isoformat()
            raw.to_parquet(out / f'{sid}-refetch.parquet', index=True)
            normalized = normalize_history(extract_symbol(raw, symbol, 1), sid, ticker)
            selected = normalized[pd.to_datetime(normalized['date']).dt.strftime('%Y-%m-%d') == DAY]
            if len(selected) != 1:
                raise ValueError('Missing or duplicate source target bar')
            replacement = selected.iloc[0].to_dict()
            validate_frame(selected)
            replacements[sid] = replacement
            fetch['replacement'] = replacement
            save()

        planned = {}
        keys = [f"backtest/ohlcv/{r['security_id']}.parquet" for r in targets]
        keys += [f'production/daily/{DAY}.parquet', 'production/rolling/latest.parquet']
        for key in keys:
            frame = pd.read_parquet(io.BytesIO(captured[key][0]))
            scoped = [r for r in targets if key == f"backtest/ohlcv/{r['security_id']}.parquet"] if key.startswith('backtest/') else targets
            repaired, changed = replace_bad_rows(frame, scoped, replacements)
            if key == 'production/rolling/latest.parquet':
                validate_frame(repaired)  # Full rolling QC, not only 34 candidates.
            if changed:
                buf = io.BytesIO()
                repaired.to_parquet(buf, index=False, compression='zstd')
                planned[key] = buf.getvalue()
            report['changes'][key] = changed
        # Detect overlapping writers before any production mutation; per-write CAS as well.
        for key, (_, etag) in captured.items():
            if s3.head_object(Bucket=bucket, Key=key)['ETag'] != etag:
                raise RuntimeError('Source changed; no repair applied')
        report['status'] = 'applying'
        save()
        for key, body in planned.items():
            s3.put_object(Bucket=bucket, Key=key, Body=body, IfMatch=captured[key][1], ContentType='application/vnd.apache.parquet')
            report['writes'].append(key)
            save()
            actual = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
            if hashlib.sha256(actual).digest() != hashlib.sha256(body).digest():
                raise RuntimeError('Repair verification failed; inspect partial writes')
        export_ready()  # Same eligibility/readiness, new immutable ready object and pointer.
        current = json.loads(s3.get_object(Bucket=bucket, Key='production/ready/current.json')['Body'].read())
        payload = s3.get_object(Bucket=bucket, Key=current['parquet_key'])['Body'].read()
        if hashlib.sha256(payload).hexdigest() != current['sha256']:
            raise RuntimeError('Published ready hash mismatch')
        validate_frame(pd.read_parquet(io.BytesIO(payload)))
        report.update(status='complete', ready_qc='passed', ready=current,
                      finished_at=datetime.now(UTC).isoformat())
        save()
        s3.put_object(Bucket=bucket, Key=prefix + '/report.json', Body=(out / 'report.json').read_bytes(), ContentType='application/json')
        print('Repair and full ready QC complete. Audit: ' + prefix)
    except Exception as exc:
        report['status'] = 'failed_or_partial' if report['writes'] else 'stopped_before_repair'
        report['error_type'] = type(exc).__name__
        save()
        print('Stopped; inspect artifact report. Error type: ' + type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
