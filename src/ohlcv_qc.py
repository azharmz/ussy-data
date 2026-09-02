"""Non-mutating OHLCV checks shared by ingestion and ready publication."""
import math

FIELDS = ('open', 'high', 'low', 'close', 'adj_close', 'volume')


def issues(row):
    try:
        values = {key: float(row[key]) for key in FIELDS}
    except (KeyError, TypeError, ValueError):
        return ['missing_or_nonnumeric']
    if not all(math.isfinite(value) for value in values.values()):
        return ['nonfinite']
    failed = []
    if any(values[key] <= 0 for key in FIELDS[:-1]):
        failed.append('nonpositive_price')
    if values['volume'] < 0:
        failed.append('negative_volume')
    if values['high'] < max(values['open'], values['close'], values['low']):
        failed.append('high_below_open_close_or_low')
    if values['low'] > min(values['open'], values['close'], values['high']):
        failed.append('low_above_open_close_or_high')
    return failed


def validate_frame(frame):
    # Column iteration avoids copying full ready datasets into dictionaries.
    if not set(FIELDS).issubset(frame.columns):
        raise ValueError('OHLCV QC: missing columns')
    bad = []
    count = 0
    for position, values in enumerate(frame[list(FIELDS)].itertuples(index=False, name=None)):
        failed = issues(dict(zip(FIELDS, values)))
        if failed:
            count += 1
            if len(bad) < 5:
                bad.append({'row_position': position, 'rules': failed})
    if count:
        raise ValueError(f'OHLCV QC rejected {count} bars; samples={bad}')
