"""Publish a ready-only rolling dataset in the private R2 bucket."""
from __future__ import annotations
from compliance import is_eligible
import hashlib
import io
import json
import os
from pathlib import Path
from datetime import UTC, datetime
from botocore.exceptions import ClientError
from ready_snapshot_guard import decide_ready_publication
from ready_late_arrival_guard import is_pure_late_arrival_window_advance, is_pure_security_set_reduction
from us_market_finalization import finalized_through


def matching_quarantine(current_ready, quarantine_path=None):
    if not current_ready:
        return None
    path=quarantine_path or (Path(__file__).resolve().parents[1]/"config"/"ready-quarantine.json")
    if not path.exists():
        return None
    payload=json.loads(path.read_text(encoding="utf-8"))
    for item in payload.get("quarantined_ready", []):
        if item.get("ready_as_of_date")==current_ready.get("as_of_date") and item.get("ready_sha256")==current_ready.get("sha256"):
            return item
    return None

def continuity_predecessor(current_ready, quarantine_path=None):
    """Use trusted predecessor when current READY lineage is quarantined."""
    if not current_ready:
        return None
    path=quarantine_path or (Path(__file__).resolve().parents[1]/"config"/"ready-quarantine.json")
    if path.exists():
        payload=json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("quarantined_ready", []):
            if item.get("ready_as_of_date")==current_ready.get("as_of_date") and item.get("ready_sha256")==current_ready.get("sha256"):
                trusted=item.get("last_known_good_as_of_date")
                if not trusted:
                    raise RuntimeError("Quarantined READY lacks last_known_good_as_of_date")
                return trusted
    return current_ready.get("as_of_date")

def select_ready_ids(readiness, membership, snapshot):
    if readiness.get("snapshot_date") != snapshot or membership.get("snapshot_date") != snapshot:
        raise ValueError("Source snapshot mismatch")
    records = membership.get("records")
    if not isinstance(records, list) or membership.get("count") != len(records):
        raise ValueError("Invalid membership records/count")
    compliant = {str(row["security_id"]) for row in records if is_eligible(row)}
    ids = readiness.get("ready_security_ids")
    if not isinstance(ids, list) or not all(isinstance(s, str) and s for s in ids):
        raise ValueError("Invalid ready_security_ids")
    selected = set(ids)
    if readiness.get("confirmed_compliant") != len(compliant):
        raise ValueError("Readiness eligibility count is stale; rebuild rolling first")
    if not selected or len(selected) != len(ids) or len(selected) != readiness.get("ready"):
        raise ValueError("Empty, duplicate, or inconsistent ready IDs")
    if not selected.issubset(compliant):
        raise ValueError("Ready IDs include non-compliant membership")
    excluded = set(readiness.get("insufficient_history_security_ids", [])) | set(readiness.get("data_unavailable_security_ids", []))
    if selected & excluded:
        raise ValueError("Ready and excluded lists overlap")
    return selected


def finalized_ready_frame(frame, cutoff=None):
    """Keep rows only through the shared safely-finalized US-session cutoff."""
    import pandas as pd
    cutoff = cutoff or finalized_through()
    dates = pd.to_datetime(frame["date"], errors="raise").dt.date
    result = frame.loc[dates <= cutoff].copy()
    if result.empty:
        raise RuntimeError(f"READY has no finalized daily bars through {cutoff}")
    dropped = len(frame) - len(result)
    if dropped:
        print(f"READY_FINALIZATION_FILTER: cutoff={cutoff} excluded_rows={dropped}", flush=True)
    return result


def terminal_date_summary(frame):
    terminal = frame.groupby("security_id")["date"].max()
    histogram = terminal.dt.date.astype(str).value_counts().sort_index()
    if histogram.empty:
        raise ValueError("READY terminal-date histogram is empty")
    mode_count = int(histogram.max())
    mode_dates = sorted(str(d) for d, n in histogram.items() if int(n) == mode_count)
    as_of = mode_dates[-1]
    max_date = str(terminal.max().date())
    if max_date > as_of:
        leading = int((terminal.dt.date.astype(str) == max_date).sum())
        raise RuntimeError(
            f"READY partial leading-edge date rejected: max={max_date} securities={leading}; "
            f"modal_as_of={as_of} securities={mode_count}"
        )
    return {
        "as_of_date": as_of,
        "as_of_security_count": mode_count,
        "terminal_date_min": str(terminal.min().date()),
        "terminal_date_max": max_date,
        "terminal_date_histogram": {str(k): int(v) for k, v in histogram.items()},
    }


def recent_session_gap_summary(frame, previous_as_of, candidate_as_of):
    """Find missing observed US-market sessions while advancing READY.

    Session dates are derived from dates actually present in the cross-sectional
    READY frame, so weekends/market holidays are not invented. Only the forward
    window after the previous canonical READY date is checked.
    """
    import pandas as pd
    if not previous_as_of or not candidate_as_of or candidate_as_of <= previous_as_of:
        return {"previous_as_of": previous_as_of, "candidate_as_of": candidate_as_of,
                "expected_sessions": [], "gap_security_count": 0, "gaps": {}}
    dates = pd.to_datetime(frame["date"], errors="raise").dt.date
    prev = pd.Timestamp(previous_as_of).date()
    cand = pd.Timestamp(candidate_as_of).date()
    expected = sorted({d for d in dates if prev < d <= cand})
    gaps = {}
    for sid, group in frame.groupby("security_id"):
        present = set(pd.to_datetime(group["date"], errors="raise").dt.date)
        terminal = max(present)
        if terminal < cand:
            continue
        missing = [d.isoformat() for d in expected if d <= terminal and d not in present]
        if missing:
            gaps[str(sid)] = missing
    return {
        "previous_as_of": previous_as_of,
        "candidate_as_of": candidate_as_of,
        "expected_sessions": [d.isoformat() for d in expected],
        "gap_security_count": len(gaps),
        "gaps": gaps,
    }


def enforce_recent_session_continuity(frame, previous_as_of, candidate_as_of):
    summary = recent_session_gap_summary(frame, previous_as_of, candidate_as_of)
    if summary["gap_security_count"]:
        sample = dict(list(summary["gaps"].items())[:20])
        raise RuntimeError(
            "READY recent-session continuity rejected: "
            f"previous_as_of={previous_as_of} candidate_as_of={candidate_as_of} "
            f"expected_sessions={summary['expected_sessions']} "
            f"gap_securities={summary['gap_security_count']} sample={sample}"
        )
    return summary

def filter_rolling(frame, readiness, selected):
    import pandas as pd
    required = ["date", "security_id", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
    if not set(required).issubset(frame.columns):
        raise ValueError("Rolling data lacks required columns")
    result = frame.loc[frame["security_id"].isin(selected), required].copy()
    if set(result["security_id"]) != selected:
        raise ValueError("Some ready IDs have no rolling data")
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    if result["date"].isna().any() or result.duplicated(["security_id", "date"]).any():
        raise ValueError("Null dates or duplicate security/date rows")
    counts = result.groupby("security_id").size()
    minimum, maximum = readiness["minimum_ready_bars"], readiness["rolling_bars_target"]
    if not 1 <= minimum <= maximum or (counts < minimum).any() or (counts > maximum).any():
        raise ValueError("Ready row counts violate configured bar thresholds")
    details = {row["security_id"]: row for row in readiness["securities"]}
    dates = result.groupby("security_id")["date"].max()
    for sid in selected:
        if sid not in details or counts[sid] != details[sid]["rolling_bars"] or dates[sid].date().isoformat() != details[sid]["last_date"]:
            raise ValueError("Rolling data and readiness details disagree")
    from ohlcv_qc import validate_frame
    validate_frame(result)
    return result.sort_values(["security_id", "date"]).reset_index(drop=True)


def main():
    import pandas as pd
    from bootstrap_ohlcv import make_s3_client
    s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]

    def read(key):
        obj = s3.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read(), obj["ETag"]

    def read_optional_json(key):
        try:
            raw, _ = read(key)
        except s3.exceptions.NoSuchKey:
            return None
        return json.loads(raw)

    current_raw, current_etag = read("universe/current.json")
    current = json.loads(current_raw); snapshot = current["snapshot_date"]
    ready_raw, ready_etag = read("production/rolling/readiness.json"); readiness = json.loads(ready_raw)
    member_key = f"universe/membership/{snapshot}.json"
    member_raw, member_etag = read(member_key)
    ids = select_ready_ids(readiness, json.loads(member_raw), snapshot)
    rolling_raw, rolling_etag = read("production/rolling/latest.parquet")
    frame = filter_rolling(pd.read_parquet(io.BytesIO(rolling_raw)), readiness, ids)
    cutoff = finalized_through()
    frame = finalized_ready_frame(frame, cutoff=cutoff)
    post_counts = frame.groupby("security_id").size()
    if set(post_counts.index.astype(str)) != ids or (post_counts < readiness["minimum_ready_bars"]).any():
        raise RuntimeError("READY finalization filter violates minimum history coverage")
    terminal = terminal_date_summary(frame)
    current_ready = read_optional_json("production/ready/current.json")
    continuity = enforce_recent_session_continuity(
        frame,
        continuity_predecessor(current_ready),
        terminal["as_of_date"],
    )
    print(f"READY finalization: finalized_through={cutoff}", flush=True)
    print("READY recent-session continuity:", json.dumps(continuity, sort_keys=True))
    print("READY terminal-date summary:", json.dumps(terminal, sort_keys=True))
    for key, etag in [("universe/current.json", current_etag), (member_key, member_etag),
                      ("production/rolling/readiness.json", ready_etag), ("production/rolling/latest.parquet", rolling_etag)]:
        if s3.head_object(Bucket=bucket, Key=key)["ETag"] != etag:
            raise RuntimeError("Source changed during export; retry after update finishes")

    buf = io.BytesIO(); frame.to_parquet(buf, engine="pyarrow", index=False, compression="zstd"); body = buf.getvalue()
    candidate_sha = hashlib.sha256(body).hexdigest()
    # Preserve one immutable canonical READY per trading date. A later provider arrival can
    # legitimately advance one security's fixed rolling window after that date was already
    # published. Only when a strict semantic comparison proves a pure forward window shift
    # with unchanged overlap do we defer it to the next trading-date snapshot. Every other
    # same-day content difference still reaches decide_ready_publication() and fails closed.
    if (current_ready and current_ready.get("as_of_date") == terminal["as_of_date"]
            and current_ready.get("sha256") != candidate_sha):
        canonical_raw, _ = read(current_ready["parquet_key"])
        canonical = pd.read_parquet(io.BytesIO(canonical_raw))
        safe_late_arrival, evidence = is_pure_late_arrival_window_advance(
            canonical, frame, terminal["as_of_date"]
        )
        if safe_late_arrival:
            print(
                "READY_SAME_DAY_LATE_ARRIVAL_DEFERRED: " + json.dumps(evidence, sort_keys=True),
                flush=True,
            )
            print(
                f"READY canonical snapshot REUSE: as_of_date={terminal['as_of_date']} "
                f"canonical_sha256={current_ready['sha256']} candidate_sha256={candidate_sha} "
                f"key={current_ready['parquet_key']}",
                flush=True,
            )
            return
        safe_reduction, reduction_evidence = is_pure_security_set_reduction(canonical, frame)
        if safe_reduction:
            print(
                "READY_SAME_DAY_SECURITY_SET_REDUCTION_DEFERRED: "
                + json.dumps(reduction_evidence, sort_keys=True),
                flush=True,
            )
            print(
                f"READY canonical snapshot REUSE: as_of_date={terminal['as_of_date']} "
                f"canonical_sha256={current_ready['sha256']} candidate_sha256={candidate_sha} "
                f"key={current_ready['parquet_key']}",
                flush=True,
            )
            return

    decision = decide_ready_publication(current_ready, terminal["as_of_date"], candidate_sha)
    if decision == "REUSE":
        print(
            f"READY canonical snapshot REUSE: as_of_date={terminal['as_of_date']} "
            f"sha256={candidate_sha} key={current_ready['parquet_key']}",
            flush=True,
        )
        print("Ready dataset unchanged: production/ready/current.json")
        return

    # One deterministic canonical object key per finalized trading date. Existing UUID-keyed
    # snapshots remain valid lineage; same-day reruns are handled above and never create another.
    key = f"production/ready/runs/{terminal['as_of_date']}.parquet"
    quarantine = matching_quarantine(current_ready)
    if quarantine and current_ready.get("as_of_date") == terminal["as_of_date"]:
        # Never overwrite or delete the quarantined immutable evidence. A repaired
        # same-date generation gets content-addressed identity after continuity passed.
        key = f"production/ready/runs/{terminal['as_of_date']}-recovery-{candidate_sha[:16]}.parquet"
        print("READY_QUARANTINE_RECOVERY: preserving bad generation and publishing "+key, flush=True)
    try:
        existing_head=s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
            raise
    else:
        existing_raw, _ = read(key)
        if hashlib.sha256(existing_raw).hexdigest() != candidate_sha:
            raise RuntimeError(f"READY immutable key conflict: {key}")
        print(f"READY immutable recovery object already exists and matches: {key}", flush=True)

    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError:
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/vnd.apache.parquet")
    if s3.head_object(Bucket=bucket, Key=key)["ContentLength"] != len(body):
        raise RuntimeError("Ready export size verification failed")
    manifest = {
        "schema_version": 2, "created_at": datetime.now(UTC).isoformat(),
        "snapshot_date": snapshot, "readiness_created_at": readiness["created_at"],
        "securities": len(ids), "rows": len(frame), "security_ids": sorted(ids),
        "minimum_ready_bars": readiness["minimum_ready_bars"], "rolling_bars_target": readiness["rolling_bars_target"],
        "parquet_key": key, "sha256": candidate_sha,
        "policy": "active_compliant_and_ready; one immutable canonical snapshot per finalized trading date; US daily bars eligible after regular close plus 90m safety buffer; modal terminal date must equal global max date",
        "finalized_through": cutoff.isoformat(),
        **terminal,
    }
    s3.put_object(Bucket=bucket, Key="production/ready/current.json", Body=json.dumps(manifest, indent=2).encode(), ContentType="application/json")
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("security_ids", "terminal_date_histogram")}, indent=2))
    print("Ready dataset: production/ready/current.json (private bucket; originals unchanged)")


if __name__ == "__main__":
    main()
