"""Rebuild eligibility from existing R2 histories only; never download Yahoo data."""
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import UTC, datetime
from compliance import POLICY


def main():
    from bootstrap_ohlcv import make_s3_client, load_membership
    s3, bucket = make_s3_client(), os.environ['R2_BUCKET_NAME']
    key = 'universe/current.json'
    obj = s3.get_object(Bucket=bucket, Key=key)
    original = obj['Body'].read()
    current = json.loads(original)
    snapshot = current['snapshot_date']
    members = load_membership(s3, bucket, snapshot)
    available = set()
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix='backtest/ohlcv/'):
        available.update(item['Key'].removeprefix('backtest/ohlcv/').removesuffix('.parquet')
                         for item in page.get('Contents', []) if item['Key'].endswith('.parquet'))
    missing = [{'security_id': str(row['security_id']), 'ticker': row['ticker']}
               for row in members if str(row['security_id']) not in available]
    report = {'snapshot_date': snapshot, 'eligibility_policy': POLICY,
              'confirmed_compliant': len(members), 'missing_histories': missing,
              'missing_count': len(missing), 'yahoo_downloads': 0}
    print(json.dumps(report, indent=2), flush=True)
    report_key = f'universe/policy_audits/{snapshot}/{os.getenv("GITHUB_RUN_ID", "local")}-{os.getenv("GITHUB_RUN_ATTEMPT", "1")}.json'
    s3.put_object(Bucket=bucket, Key=report_key, Body=json.dumps(report).encode(), ContentType='application/json')
    source_dir = Path(__file__).resolve().parent
    subprocess.run([sys.executable, str(source_dir / 'build_rolling.py'), '--snapshot-date', snapshot,
                    '--rolling-bars', '300', '--minimum-ready-bars', '250'], check=True)
    if s3.head_object(Bucket=bucket, Key=key)['ETag'] != obj['ETag']:
        raise RuntimeError('Universe pointer changed; rerun after other updates finish')
    # Preserve source metadata and save the old pointer; no source labels or histories changed.
    s3.put_object(Bucket=bucket, Key=report_key.removesuffix('.json') + '-previous-pointer.json',
                  Body=original, ContentType='application/json')
    current.update(confirmed_compliant=len(members), eligibility_policy=POLICY,
                   updated_at=datetime.now(UTC).isoformat())
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(current).encode(),
                  ContentType='application/json', IfMatch=obj['ETag'])
    print('Missing history report: ' + report_key)


if __name__ == '__main__':
    main()
