"""Read the versioned ready dataset from a trusted private R2 bucket."""
import hashlib
import io
import json


def load_ready(s3, bucket):
    import pandas as pd
    manifest = json.loads(s3.get_object(Bucket=bucket, Key="production/ready/current.json")["Body"].read())
    key = manifest["parquet_key"]
    if manifest.get("schema_version") != 1 or not key.startswith("production/ready/runs/"):
        raise ValueError("Invalid ready manifest")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if hashlib.sha256(body).hexdigest() != manifest["sha256"]:
        raise ValueError("Ready Parquet checksum mismatch")
    frame = pd.read_parquet(io.BytesIO(body))
    if len(frame) != manifest["rows"] or set(frame["security_id"]) != set(manifest["security_ids"]):
        raise ValueError("Ready manifest/data mismatch")
    return frame, manifest
