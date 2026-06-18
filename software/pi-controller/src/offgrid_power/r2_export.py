"""Store-and-forward metric export to an S3-compatible object store.

Batches serialize to Apache Parquet via pyarrow. The exporter shipped
gzipped NDJSON while the Pi ran 32-bit armv7l (no pyarrow/duckdb wheels
existed for that platform); on the 64-bit OS the serializer is Parquet,
which is what the DuckDB analysis side reads natively.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import sqlite3
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import pyarrow as pa
import pyarrow.parquet as pq

from .metrics import initialize_metrics_db


@dataclass(frozen=True)
class R2Config:
    access_key_id: str
    secret_access_key: str
    bucket: str
    site_id: str
    account_id: str = ""
    prefix: str = "metrics"
    endpoint_url: str | None = None
    region: str = "auto"

    @property
    def endpoint(self) -> str:
        if self.endpoint_url:
            return self.endpoint_url.rstrip("/")
        if not self.account_id:
            raise ValueError("R2 account id or S3-compatible endpoint URL is required")
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


@dataclass(frozen=True)
class ExportBatch:
    batch_id: str
    object_key: str
    body: bytes
    records: tuple[tuple[str, int], ...]
    row_count: int
    min_row_id: int
    max_row_id: int
    content_sha256: str


@dataclass(frozen=True)
class ExportResult:
    batch_id: str | None
    object_key: str | None
    row_count: int
    uploaded: bool


class R2PutClient:
    def __init__(self, config: R2Config) -> None:
        self.config = config

    def put_object(self, key: str, body: bytes, content_type: str) -> None:
        endpoint = self.config.endpoint
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Object-store endpoint must be an https URL")
        path = f"/{quote(self.config.bucket, safe='')}/{quote(key, safe='/')}"
        url = f"{endpoint}{path}"
        content_sha256 = hashlib.sha256(body).hexdigest()
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        headers = {
            "content-type": content_type,
            "host": parsed.netloc,
            "x-amz-content-sha256": content_sha256,
            "x-amz-date": amz_date,
        }
        authorization = self._authorization("PUT", path, headers, content_sha256, amz_date, date_stamp)
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": authorization,
                "Content-Type": content_type,
                "Host": parsed.netloc,
                "X-Amz-Content-Sha256": content_sha256,
                "X-Amz-Date": amz_date,
            },
            method="PUT",
        )
        try:
            with urlopen(request, timeout=30) as response:
                if response.status not in (200, 201):
                    raise RuntimeError(f"Object-store PUT failed with HTTP {response.status}")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Object-store PUT failed with HTTP {exc.code}: {detail}") from exc

    def _authorization(
        self,
        method: str,
        canonical_uri: str,
        headers: dict[str, str],
        payload_hash: str,
        amz_date: str,
        date_stamp: str,
    ) -> str:
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in sorted(headers))
        canonical_request = "\n".join(
            [
                method,
                canonical_uri,
                "",
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        credential_scope = f"{date_stamp}/{self.config.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _signing_key(self.config.secret_access_key, date_stamp, self.config.region)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        credential = f"{self.config.access_key_id}/{credential_scope}"
        return (
            "AWS4-HMAC-SHA256 "
            f"Credential={credential}, SignedHeaders={signed_headers}, Signature={signature}"
        )


def export_metrics_once(db_path: str | Path, config: R2Config, limit: int = 5000) -> ExportResult:
    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.execute("PRAGMA busy_timeout = 60000")
        initialize_metrics_db(connection)
        batch = build_export_batch(connection, config.site_id, config.prefix, limit=limit)
        if batch is None:
            return ExportResult(batch_id=None, object_key=None, row_count=0, uploaded=False)

    R2PutClient(config).put_object(batch.object_key, batch.body, "application/vnd.apache.parquet")

    uploaded_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path, timeout=60) as connection:
        connection.execute("PRAGMA busy_timeout = 60000")
        initialize_metrics_db(connection)
        mark_batch_exported(connection, batch, uploaded_at)
    return ExportResult(
        batch_id=batch.batch_id,
        object_key=batch.object_key,
        row_count=batch.row_count,
        uploaded=True,
    )


# Per-table export streams. Each batch holds one table and one UTC capture date,
# so every object has a uniform schema and lands under a hive-style date
# partition that DuckDB can prune. ``date(captured_at)`` keeps legacy local-
# offset rows and new UTC rows grouped by the actual instant, not raw text.
# (e.g. metrics/samples/date=2026-06-12/<ts>-<batch>.parquet).
_EXPORT_TABLES = ("samples", "events")

# Parquet column types per table. Each batch is single-table, so the
# schema is uniform per object. tags is a map (uniformly string->string);
# event detail is heterogeneous across event types, so it stays a JSON
# string column rather than a fixed struct.
_SAMPLE_SCHEMA = pa.schema(
    [
        ("record_type", pa.string()),
        ("site_id", pa.string()),
        ("record_id", pa.string()),
        ("local_row_id", pa.int64()),
        ("captured_at", pa.string()),
        ("source", pa.string()),
        ("metric", pa.string()),
        ("value", pa.float64()),
        ("text", pa.string()),
        ("unit", pa.string()),
        ("tags", pa.map_(pa.string(), pa.string())),
    ]
)
_EVENT_SCHEMA = pa.schema(
    [
        ("record_type", pa.string()),
        ("site_id", pa.string()),
        ("record_id", pa.string()),
        ("local_row_id", pa.int64()),
        ("captured_at", pa.string()),
        ("source", pa.string()),
        ("event", pa.string()),
        ("detail", pa.string()),
    ]
)


def build_export_batch(
    connection: sqlite3.Connection,
    site_id: str,
    prefix: str,
    limit: int = 5000,
) -> ExportBatch | None:
    for table in _EXPORT_TABLES:
        day = connection.execute(
            f"SELECT MIN(date(captured_at)) FROM {table} WHERE exported_at IS NULL"
        ).fetchone()[0]
        if day is None:
            continue
        records = _unexported_records(connection, table, day, limit)
        payload_records = [_record_to_payload(record, site_id) for record in records]
        body = _serialize_parquet(table, payload_records)
        content_sha256 = hashlib.sha256(body).hexdigest()
        batch_id = content_sha256[:32]
        created_at = datetime.now(timezone.utc)
        object_key = (
            f"{prefix.strip('/')}/{table}/date={day}/"
            f"{created_at:%Y%m%dT%H%M%SZ}-{batch_id}.parquet"
        )
        return ExportBatch(
            batch_id=batch_id,
            object_key=object_key,
            body=body,
            records=tuple((record["record_type"], record["id"]) for record in records),
            row_count=len(payload_records),
            min_row_id=min(record["id"] for record in records),
            max_row_id=max(record["id"] for record in records),
            content_sha256=content_sha256,
        )
    return None


def _serialize_parquet(table: str, payload_records: list[dict]) -> bytes:
    schema, columns = _arrow_columns(table, payload_records)
    arrays = [pa.array(columns[field.name], type=field.type) for field in schema]
    arrow_table = pa.Table.from_arrays(arrays, schema=schema)
    buffer = io.BytesIO()
    pq.write_table(arrow_table, buffer, compression="snappy")
    return buffer.getvalue()


def _arrow_columns(table: str, payload_records: list[dict]) -> tuple[pa.Schema, dict[str, list]]:
    if table == "samples":
        return _SAMPLE_SCHEMA, {
            "record_type": [record["record_type"] for record in payload_records],
            "site_id": [record["site_id"] for record in payload_records],
            "record_id": [record["record_id"] for record in payload_records],
            "local_row_id": [record["local_row_id"] for record in payload_records],
            "captured_at": [record["captured_at"] for record in payload_records],
            "source": [record["source"] for record in payload_records],
            "metric": [record["metric"] for record in payload_records],
            "value": [record["value"] for record in payload_records],
            "text": [record["text"] for record in payload_records],
            "unit": [record["unit"] for record in payload_records],
            "tags": [record["tags"] for record in payload_records],
        }
    return _EVENT_SCHEMA, {
        "record_type": [record["record_type"] for record in payload_records],
        "site_id": [record["site_id"] for record in payload_records],
        "record_id": [record["record_id"] for record in payload_records],
        "local_row_id": [record["local_row_id"] for record in payload_records],
        "captured_at": [record["captured_at"] for record in payload_records],
        "source": [record["source"] for record in payload_records],
        "event": [record["event"] for record in payload_records],
        "detail": [
            json.dumps(record["detail"], sort_keys=True, separators=(",", ":"))
            for record in payload_records
        ],
    }


def mark_batch_exported(connection: sqlite3.Connection, batch: ExportBatch, uploaded_at: str) -> None:
    by_table = {"sample": [], "event": []}
    for record_type, record_id in batch.records:
        by_table[record_type].append(record_id)
    with connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO export_batches (
                batch_id, created_at, uploaded_at, object_key, record_count, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                batch.batch_id,
                datetime.now(timezone.utc).isoformat(),
                uploaded_at,
                batch.object_key,
                batch.row_count,
                "uploaded",
            ),
        )
        connection.executemany(
            "UPDATE samples SET exported_at = ?, export_batch_id = ? WHERE id = ?",
            [(uploaded_at, batch.batch_id, record_id) for record_id in by_table["sample"]],
        )
        connection.executemany(
            "UPDATE events SET exported_at = ?, export_batch_id = ? WHERE id = ?",
            [(uploaded_at, batch.batch_id, record_id) for record_id in by_table["event"]],
        )


def _unexported_records(connection: sqlite3.Connection, table: str, day: str, limit: int) -> list[dict]:
    if table == "samples":
        rows = connection.execute(
            """
            SELECT id, sample_id, captured_at, source, metric, value, text, unit, tags_json
            FROM samples
            WHERE exported_at IS NULL AND date(captured_at) = ?
            ORDER BY id
            LIMIT ?
            """,
            (day, limit),
        ).fetchall()
        return [
            {
                "record_type": "sample",
                "id": row[0],
                "sample_id": row[1],
                "captured_at": row[2],
                "source": row[3],
                "metric": row[4],
                "value": row[5],
                "text": row[6],
                "unit": row[7],
                "tags_json": row[8],
            }
            for row in rows
        ]
    rows = connection.execute(
        """
        SELECT id, event_id, captured_at, source, event, detail_json
        FROM events
        WHERE exported_at IS NULL AND date(captured_at) = ?
        ORDER BY id
        LIMIT ?
        """,
        (day, limit),
    ).fetchall()
    return [
        {
            "record_type": "event",
            "id": row[0],
            "event_id": row[1],
            "captured_at": row[2],
            "source": row[3],
            "event": row[4],
            "detail_json": row[5],
        }
        for row in rows
    ]


def _record_to_payload(record: dict, site_id: str) -> dict:
    if record["record_type"] == "sample":
        return {
            "record_type": "sample",
            "site_id": site_id,
            "record_id": record["sample_id"],
            "local_row_id": record["id"],
            "captured_at": record["captured_at"],
            "source": record["source"],
            "metric": record["metric"],
            "value": record["value"],
            "text": record["text"],
            "unit": record["unit"],
            "tags": _parse_tags(record["tags_json"]),
        }
    return {
        "record_type": "event",
        "site_id": site_id,
        "record_id": record["event_id"],
        "local_row_id": record["id"],
        "captured_at": record["captured_at"],
        "source": record["source"],
        "event": record["event"],
        "detail": _parse_json_object(record["detail_json"]),
    }


def _parse_json_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    if isinstance(parsed, dict):
        return parsed
    return {"raw": value}


def _parse_tags(tags_json: str) -> dict[str, str]:
    try:
        parsed = json.loads(tags_json or "{}")
    except json.JSONDecodeError:
        return {"raw": tags_json or ""}
    if not isinstance(parsed, dict):
        return {"raw": tags_json or ""}
    return {str(key): str(value) for key, value in parsed.items()}


def _signing_key(secret_access_key: str, date_stamp: str, region: str) -> bytes:
    key = f"AWS4{secret_access_key}".encode("utf-8")
    date_key = hmac.new(key, date_stamp.encode("utf-8"), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode("utf-8"), hashlib.sha256).digest()
    service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()
