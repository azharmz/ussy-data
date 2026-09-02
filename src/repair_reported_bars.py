"""Repair 37 reviewed pairs; reuse 34 captured fetches and download only 3."""
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
NEW_IDS = {'KYG2124J1085', 'KYG7049C1042', 'US7189681007'}


def load_cached(cached, targets):
    expected = {r['security_id']: r for r in targets if r['security_id'] not in NEW_IDS}
    result = {}
    for fetch in cached['fetches']:
        sid = fetch['security_id']
        row = fetch.get('replacement', {})
        if sid not in expected or sid in result:
            raise ValueError('Unexpected or duplicate cached identity')
        if (row.get('security_id') != sid or row.get('ticker') != expected[sid]['ticker']
                or str(row.get('date')) != DAY + ' 00:00:00' or issues(row)):
            raise ValueError('Invalid cached replacement identity/date/OHLCV')
        params = fetch.get('parameters', {})
        if (params.get('start') != DAY or params.get('end') != '2026-09-02'
                or params.get('interval') != '1d' or params.get('auto_adjust') is not False
                or params.get('repair') is not False or not fetch.get('finished_at')):
            raise ValueError('Unexpected cached fetch provenance')
        result[sid] = row
    if set(result) != set(expected) or len(result) != 34:
        raise ValueError('Expected exactly 34 cached replacements; no automatic refetch fallback')
    return result


def daily_targets(frame, targets):
    """Daily may not contain newly bootstrapped securities; do not insert rows."""
    import pandas as pd
    ids = set(frame.loc[pd.to_datetime(frame['date']).dt.strftime('%Y-%m-%d') == DAY, 'security_id'])
    return [row for row in targets if row['security_id'] in ids]


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
              'evidence_note': '34 captured fetches reused, 3 fresh fetches; neither proves original Yahoo response.',
              'stage': 'scope_validation'}
    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    save()
    try:
        logging.disable(logging.CRITICAL)
        with (Path(__file__).resolve().parents[1] / 'audits/2026-09-01-invalid-bars.csv').open(encoding='utf-8') as stream:
            targets = list(csv.DictReader(stream))
        if len(targets) != 37 or len({r['security_id'] for r in targets}) != 37 or any(r['date'] != DAY for r in targets):
            raise ValueError('Unexpected repair scope')
        cache_path = Path(__file__).resolve().parents[1] / 'audits/2026-09-01-cached-fetches.json'
        cache_body = cache_path.read_bytes()
        cached = json.loads(cache_body)
        replacements = load_cached(cached, targets)
        (out / 'cached-fetches.json').write_bytes(cache_body)
        report['reused_count'] = len(replacements)
        report['cached_provenance'] = {k: v for k, v in cached.items() if k != 'fetches'}
        report['cached_sha256'] = hashlib.sha256(cache_body).hexdigest()
        report['stage'] = 'capture'
        s3, bucket = make_s3_client(), os.environ['R2_BUCKET_NAME']
        prefix = f'audit/ohlcv-repairs/{uuid4().hex}'
        report['backup_prefix'] = prefix
        captured = {}
        def capture(key):
            report['active_key'] = key
            save()
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
        report['stage'] = 'backup'
        for key, (body, _) in captured.items():
            backup = prefix + '/before/' + key
            s3.put_object(Bucket=bucket, Key=backup, Body=body, IfNoneMatch='*')
            actual = s3.get_object(Bucket=bucket, Key=backup)['Body'].read()
            if hashlib.sha256(actual).digest() != hashlib.sha256(body).digest():
                raise RuntimeError('Backup verification failed')
        report['status'] = 'refetching'
        report['stage'] = 'fetch_additional_three'
        params = dict(start=DAY, end='2026-09-02', interval='1d', auto_adjust=False, actions=False,
                      progress=False, threads=False, timeout=30, prepost=False, repair=False)
        fresh_targets = [row for row in targets if row['security_id'] in NEW_IDS]
        if len(fresh_targets) != 3:
            raise ValueError('Expected exactly three new fetch targets')
        for index, row in enumerate(fresh_targets):
            if index:
                time.sleep(3)
            sid, ticker = row['security_id'], row['ticker']
            symbol = yahoo_symbol(ticker, sid)
            fetch = {'security_id': sid, 'ticker': ticker, 'symbol': symbol, 'parameters': params,
                     'started_at': datetime.now(UTC).isoformat()}
            report['fetches'].append(fetch)
            report['active_ticker'] = ticker
            print(f'Checking {index + 1}/3: {ticker} (34 fetches reused)', flush=True)
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
        report['stage'] = 'plan_repair'
        keys = [f"backtest/ohlcv/{r['security_id']}.parquet" for r in targets]
        keys += [f'production/daily/{DAY}.parquet', 'production/rolling/latest.parquet']
        for key in keys:
            report['active_key'] = key
            save()
            frame = pd.read_parquet(io.BytesIO(captured[key][0]))
            scoped = [r for r in targets if key == f"backtest/ohlcv/{r['security_id']}.parquet"] if key.startswith('backtest/') else targets
            if key.startswith('production/daily/'):
                scoped = daily_targets(frame, targets)
                report['daily_absent_ids_unchanged'] = sorted({r['security_id'] for r in targets} - {r['security_id'] for r in scoped})
            repaired, changed = replace_bad_rows(frame, scoped, replacements)
            if key == 'production/rolling/latest.parquet':
                report['stage'] = 'full_rolling_qc'
                report['remaining_invalid'] = [dict(row, qc_rules=issues(row))
                    for row in repaired.to_dict(orient='records') if issues(row)]
                save()
                validate_frame(repaired)  # Full rolling QC, not only the reviewed candidates.
            if changed:
                buf = io.BytesIO()
                repaired.to_parquet(buf, index=False, compression='zstd')
                planned[key] = buf.getvalue()
            report['changes'][key] = changed
        # Detect overlapping writers before any production mutation; per-write CAS as well.
        report['stage'] = 'source_stability'
        for key, (_, etag) in captured.items():
            if s3.head_object(Bucket=bucket, Key=key)['ETag'] != etag:
                raise RuntimeError('Source changed; no repair applied')
        report['status'] = 'applying'
        report['stage'] = 'conditional_writes'
        save()
        for key, body in planned.items():
            report['active_key'] = key
            save()
            s3.put_object(Bucket=bucket, Key=key, Body=body, IfMatch=captured[key][1], ContentType='application/vnd.apache.parquet')
            report['writes'].append(key)
            save()
            actual = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
            if hashlib.sha256(actual).digest() != hashlib.sha256(body).digest():
                raise RuntimeError('Repair verification failed; inspect partial writes')
        report['stage'] = 'export_ready'
        save()
        export_ready()  # Same eligibility/readiness, new immutable ready object and pointer.
        report['stage'] = 'published_ready_qc'
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
        # Validation errors are useful; do not expose transport or credential messages.
        if isinstance(exc, ValueError):
            message = str(exc)
            for name in ('R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_ENDPOINT', 'R2_BUCKET_NAME'):
                value = os.getenv(name)
                if value:
                    message = message.replace(value, '[REDACTED]')
            report['validation_error'] = message[:1500]
        save()
        print('Stopped; inspect artifact report. Error type: ' + type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
