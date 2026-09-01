from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from bootstrap_ohlcv import make_s3_client


PATTERN = re.compile(r"musaffa_(snapshot|security_master|membership)_(\d{4}-\d{2}-\d{2})\.json$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a Musaffa universe snapshot and calculate membership changes")
    parser.add_argument("--snapshot-date", default="latest")
    parser.add_argument("--universe-dir", default="universe")
    return parser.parse_args()


def available_dates(directory: Path) -> set[str]:
    found: dict[str, set[str]] = {}
    for path in directory.glob("musaffa_*.json"):
        match = PATTERN.fullmatch(path.name)
        if match:
            kind, date = match.groups()
            found.setdefault(date, set()).add(kind)
    return {date for date, kinds in found.items() if kinds == {"snapshot", "security_master", "membership"}}


def load_local(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload.get("records"), list) or payload.get("count") != len(payload["records"]):
        raise ValueError(f"Invalid count or records array: {path}")
    return payload


def read_r2_json(s3, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def confirmed(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row["security_id"]): row
        for row in records
        if row.get("security_id")
        and row.get("ticker")
        and row.get("sharia_compliance") == "COMPLIANT"
        and row.get("musaffaHalalRating") == "COMPLIANT"
    }


def main() -> None:
    args = parse_args()
    directory = Path(args.universe_dir)
    dates = available_dates(directory)
    if not dates:
        raise ValueError("No complete Musaffa snapshot/security_master/membership file set found")
    snapshot_date = max(dates) if args.snapshot_date == "latest" else args.snapshot_date
    if snapshot_date not in dates:
        raise ValueError(f"Complete Musaffa file set not found for {snapshot_date}")

    files = {
        "snapshot": directory / f"musaffa_snapshot_{snapshot_date}.json",
        "security_master": directory / f"musaffa_security_master_{snapshot_date}.json",
        "membership": directory / f"musaffa_membership_{snapshot_date}.json",
    }
    payloads = {kind: load_local(path) for kind, path in files.items()}
    for kind, payload in payloads.items():
        if payload.get("snapshot_date") != snapshot_date:
            raise ValueError(f"{kind} snapshot_date does not match filename")

    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    pointer = read_r2_json(s3, bucket, "universe/current.json")
    previous_date = pointer.get("snapshot_date") if pointer else None
    previous_payload = (
        read_r2_json(s3, bucket, f"universe/membership/{previous_date}.json")
        if previous_date and previous_date != snapshot_date
        else None
    )

    current = confirmed(payloads["membership"]["records"])
    previous = confirmed(previous_payload["records"]) if previous_payload else current
    added_ids = sorted(set(current) - set(previous))
    removed_ids = sorted(set(previous) - set(current))
    common_ids = sorted(set(current) & set(previous))
    ticker_changes = [
        {
            "security_id": security_id,
            "old_ticker": previous[security_id].get("ticker"),
            "new_ticker": current[security_id].get("ticker"),
        }
        for security_id in common_ids
        if previous[security_id].get("ticker") != current[security_id].get("ticker")
    ]

    destinations = {
        "snapshot": f"universe/raw/{snapshot_date}/musaffa_snapshot.json",
        "security_master": f"universe/security_master/{snapshot_date}.json",
        "membership": f"universe/membership/{snapshot_date}.json",
    }
    for kind, key in destinations.items():
        s3.put_object(Bucket=bucket, Key=key, Body=files[kind].read_bytes(), ContentType="application/json")

    change = {
        "created_at": datetime.now(UTC).isoformat(),
        "previous_snapshot_date": previous_date,
        "snapshot_date": snapshot_date,
        "baseline": previous_payload is None,
        "confirmed_compliant": len(current),
        "added": [current[security_id] for security_id in added_ids],
        "removed": [previous[security_id] for security_id in removed_ids],
        "ticker_changes": ticker_changes,
    }
    change_key = f"universe/changes/{snapshot_date}.json"
    s3.put_object(Bucket=bucket, Key=change_key, Body=json.dumps(change, indent=2).encode(), ContentType="application/json")

    new_pointer = {
        "updated_at": datetime.now(UTC).isoformat(),
        "snapshot_date": snapshot_date,
        "confirmed_compliant": len(current),
        "membership_key": destinations["membership"],
        "security_master_key": destinations["security_master"],
        "raw_snapshot_key": destinations["snapshot"],
        "change_key": change_key,
    }
    s3.put_object(Bucket=bucket, Key="universe/current.json", Body=json.dumps(new_pointer, indent=2).encode(), ContentType="application/json")

    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"snapshot_date={snapshot_date}\n")
            output.write(f"added_count={len(added_ids)}\n")
    print(json.dumps({**new_pointer, "added": len(added_ids), "removed": len(removed_ids), "ticker_changes": len(ticker_changes)}, indent=2))


if __name__ == "__main__":
    main()


