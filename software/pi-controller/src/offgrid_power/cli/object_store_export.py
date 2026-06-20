"""Export queued metric samples to S3-compatible object storage."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

from offgrid_power.object_store_export import ObjectStoreConfig, export_metrics_once


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload unexported metric samples to an S3-compatible bucket.")
    parser.add_argument("--metrics-db-path", default=os.getenv("METRICS_DB_PATH", "data/metrics.sqlite"))
    parser.add_argument("--access-key-id", default=env_first("B2_APPLICATION_KEY_ID", "S3_ACCESS_KEY_ID"))
    parser.add_argument(
        "--secret-access-key",
        default=env_first("B2_APPLICATION_KEY", "S3_SECRET_ACCESS_KEY"),
    )
    parser.add_argument("--bucket", default=env_first("B2_BUCKET", "S3_BUCKET"))
    parser.add_argument("--site-id", default=env_first("B2_SITE_ID", "S3_SITE_ID", default="cabin"))
    parser.add_argument("--prefix", default=env_first("B2_PREFIX", "S3_PREFIX", default="metrics"))
    parser.add_argument("--endpoint-url", default=env_first("B2_ENDPOINT_URL", "S3_ENDPOINT_URL"))
    parser.add_argument("--region", default=env_first("B2_REGION", "S3_REGION", default="auto"))
    parser.add_argument("--limit", type=int, default=int(env_first("B2_EXPORT_LIMIT", "S3_EXPORT_LIMIT", default="5000")))
    parser.add_argument(
        "--max-batches",
        type=int,
        default=int(env_first("B2_EXPORT_MAX_BATCHES", "S3_EXPORT_MAX_BATCHES", default="1")),
        help="Upload up to this many batches before exiting; use 0 to run until the backlog is empty",
    )
    parser.add_argument(
        "--sleep-between-batches",
        type=float,
        default=float(env_first("B2_EXPORT_SLEEP_SECONDS", "S3_EXPORT_SLEEP_SECONDS", default="0")),
        help="Optional delay between successful batch uploads",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    missing = [
        name
        for name, value in [
            ("access key id", args.access_key_id),
            ("secret access key", args.secret_access_key),
            ("bucket", args.bucket),
            ("endpoint URL", args.endpoint_url),
        ]
        if not value
    ]
    if missing:
        print(f"Missing required object-store configuration: {', '.join(missing)}")
        return 2

    config = ObjectStoreConfig(
        access_key_id=args.access_key_id,
        secret_access_key=args.secret_access_key,
        bucket=args.bucket,
        site_id=args.site_id,
        prefix=args.prefix,
        endpoint_url=args.endpoint_url or None,
        region=args.region,
    )
    uploaded_batches = 0
    uploaded_rows = 0
    while args.max_batches == 0 or uploaded_batches < args.max_batches:
        result = export_metrics_once(Path(args.metrics_db_path), config, limit=args.limit)
        if not result.uploaded:
            if uploaded_batches == 0:
                print("No unexported metric records")
            else:
                print(f"Export complete after {uploaded_batches} batches and {uploaded_rows} metric records")
            return 0
        uploaded_batches += 1
        uploaded_rows += result.row_count
        print(f"Uploaded {result.row_count} metric records to {result.object_key}")
        if args.sleep_between_batches > 0 and (args.max_batches == 0 or uploaded_batches < args.max_batches):
            time.sleep(args.sleep_between_batches)

    print(f"Stopped after {uploaded_batches} batches and {uploaded_rows} metric records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
