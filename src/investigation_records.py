"""Attach documented findings to matching queue issues; never change eligibility."""
import json
from pathlib import Path
from urllib.parse import urlparse

MARKET_CATEGORIES = {'DATA_UNAVAILABLE', 'INSUFFICIENT_HISTORY', 'DAILY_UPDATE_FAILED'}


def load_records():
    path = Path(__file__).resolve().parents[1] / 'config/investigation-records.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    if doc.get('schema_version') != 1 or not isinstance(doc.get('records'), list):
        raise ValueError('Invalid investigation registry')
    seen = set()
    for record in doc['records']:
        key = (record['security_id'], record['ticker'], record['scope'], record['snapshot_date'])
        if key in seen:
            raise ValueError('Duplicate investigation record')
        seen.add(key)
        url = urlparse(record['source_url'])
        if url.scheme != 'https' or not url.netloc or not record.get('reviewed_at') or not record.get('finding'):
            raise ValueError('Investigation needs dated evidence and HTTPS source')
        if record['scope'] != 'market_data' or record.get('resolution') != 'FOLLOW_UP_REQUIRED':
            raise ValueError('Unsupported investigation scope/resolution')
    return doc['records']


def attach_investigations(queue, records, snapshot):
    lookup = {(r['security_id'], r['ticker'], r['scope'], r['snapshot_date']): r for r in records}
    result = []
    for original in queue:
        row = dict(original)
        scope = 'market_data' if row['category'] in MARKET_CATEGORIES else 'screening'
        record = lookup.get((row.get('security_id'), row.get('ticker'), scope, snapshot))
        row['investigation'] = {'status': 'DETECTED_ONLY', 'scope': scope,
            'message': 'Belum ada catatan investigasi untuk jenis masalah dan snapshot ini.'}
        if record:
            row['investigation'] = {'status': 'REVIEWED_FOLLOW_UP_REQUIRED', 'scope': scope,
                'reviewed_at': record['reviewed_at'], 'event_date': record.get('event_date'),
                'finding': record['finding'], 'detail': record.get('detail'),
                'source_url': record['source_url'],
                'message': 'Sudah diteliti; temuan terdokumentasi, tindak lanjut belum dinyatakan selesai.'}
        result.append(row)
    return result
