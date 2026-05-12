#!/usr/bin/env python3
"""Bulk-upload the license-plate splits to MinIO (the local S3 store).

Skips the browser multipart upload, which is painfully slow for the
~2,650 image files in the valid+test splits. Pushes the files in
parallel to the local MinIO bucket at a fixed, project-agnostic path:

  s3://datasets/sample-data/license-plates/{split}/...

After this finishes:
  1. In the chat, click the attach (+) button → "Browse S3"
  2. Navigate to `sample-data/license-plates/{split}/`
  3. Select the folder — the existing attach-from-S3 flow copies it
     into your active project's Modal Volume so the agent can read it.

Why not push straight to a project? Two reasons:
  • Decoupling the upload from a project_id means you can stage the
    dataset once and reuse it across many projects/experiments.
  • The existing UI already wires up "import from S3 → project" via
    `POST /api/experiments/{exp}/attach`, so this script doesn't need
    to know anything about projects.

Prerequisites:
  • The local docker stack is running (so MinIO at :9000 is reachable)

Usage:
  # Upload everything we have locally
  python upload_to_s3.py

  # One split only
  python upload_to_s3.py --split test

  # Re-upload (overwrite) even if files exist in the bucket
  python upload_to_s3.py --force
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# These match docker-compose.yml's MinIO defaults. Override via env if
# you've reconfigured the stack.
MINIO_ENDPOINT = os.environ.get("S3_ENDPOINT_EXTERNAL", "http://localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin")
MINIO_BUCKET = "datasets"

# Where in the bucket the splits land. Stable + project-agnostic so the
# S3 browser in the UI can find them under one predictable folder.
DATASET_PREFIX = "sample-data/license-plates"

# Splits in upload order — small ones first so a cancelled run still
# leaves something usable.
SPLIT_ORDER = ["valid-mini", "test", "valid", "train"]


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".json": "application/json",
        ".txt": "text/plain",
    }.get(suffix, "application/octet-stream")


def _ensure_bucket(s3) -> None:
    from botocore.exceptions import ClientError

    try:
        s3.head_bucket(Bucket=MINIO_BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=MINIO_BUCKET)
        print(f"[s3] created bucket {MINIO_BUCKET!r}")


def _existing_keys(s3, prefix: str) -> set[str]:
    """List keys already under `prefix/` so we can skip them by default."""
    keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=MINIO_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            keys.add(obj["Key"])
    return keys


def upload_split(s3, split_dir: Path, split: str, force: bool) -> None:
    files = sorted(p for p in split_dir.iterdir() if p.is_file())
    prefix = f"{DATASET_PREFIX}/{split}"
    skip = set() if force else _existing_keys(s3, prefix + "/")

    targets = [p for p in files if f"{prefix}/{p.name}" not in skip]
    skipped = len(files) - len(targets)
    print(
        f"[{split}] s3://{MINIO_BUCKET}/{prefix}/  "
        f"— {len(targets)} to upload"
        + (f", {skipped} already present (--force to overwrite)" if skipped else "")
    )
    if not targets:
        return

    started = time.monotonic()
    done = 0

    def _put(p: Path) -> None:
        s3.upload_file(
            str(p),
            MINIO_BUCKET,
            f"{prefix}/{p.name}",
            ExtraArgs={"ContentType": _content_type(p)},
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(_put, p) for p in targets]
        for fut in as_completed(futures):
            fut.result()
            done += 1
            if done % 50 == 0 or done == len(targets):
                pct = 100.0 * done / len(targets)
                rate = done / max(0.001, time.monotonic() - started)
                sys.stdout.write(
                    f"\r  {done:5d}/{len(targets):5d} ({pct:5.1f}%) {rate:5.1f} files/s"
                )
                sys.stdout.flush()
    sys.stdout.write("\n")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--split",
        action="append",
        choices=SPLIT_ORDER,
        help="Split(s) to upload. Default: every split present locally.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files in the bucket even if a same-named key exists.",
    )
    args = p.parse_args()

    root = Path(__file__).resolve().parent
    splits = args.split or [s for s in SPLIT_ORDER if (root / s).exists()]
    if not splits:
        print("no splits found locally — run download.py first")
        return 1

    print(f"source:   {root}")
    print(f"target:   s3://{MINIO_BUCKET}/{DATASET_PREFIX}/  (endpoint {MINIO_ENDPOINT})")
    print(f"splits:   {', '.join(splits)}")
    print()

    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
    )
    _ensure_bucket(s3)

    for split in splits:
        split_dir = root / split
        if not split_dir.exists():
            print(f"[skip] {split}/ not present locally — run download.py first")
            continue
        upload_split(s3, split_dir, split, args.force)
        print()

    print(
        "Done. In the chat, click + → 'Browse S3' and navigate to "
        f"{DATASET_PREFIX}/<split>/ to attach into a project."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
