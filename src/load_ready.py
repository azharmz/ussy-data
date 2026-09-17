"""Read the versioned ready dataset from a trusted private R2 bucket."""
import hashlib
import io
import json


def load_ready(s3, bucket):
    import pandas as pd
    manifest = json.loads(s3.get_object(Bucket=bucket, Key="production/ready/current.json")["Body"].read())
    key = manifest["parquet_key"]
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2} or not key.startswith("production/ready/runs/"):
        raise ValueError("Invalid ready manifest")
    if schema_version == 2:
        required = {"as_of_date", "as_of_security_count", "terminal_date_min", "terminal_date_max"}
        if not required.issubset(manifest):
            raise ValueError("Invalid ready v2 manifest")
        if manifest["terminal_date_max"] != manifest["as_of_date"]:
            raise ValueError("READY v2 as-of/max terminal-date mismatch")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if hashlib.sha256(body).hexdigest() != manifest["sha256"]:
        raise ValueError("Ready Parquet checksum mismatch")
    frame = pd.read_parquet(io.BytesIO(body))
    if len(frame) != manifest["rows"] or set(frame["security_id"]) != set(manifest["security_ids"]):
        raise ValueError("Ready manifest/data mismatch")
    return frame, manifest
