"""S3 browser endpoints for navigating external S3 buckets."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from config import settings
from schemas import UploadResponse
from services.s3_client import get_s3_client, get_s3_external_endpoint
from services.volume import should_ignore_workspace_path

logger = logging.getLogger(__name__)
router = APIRouter()

# Keys the app itself writes live under this prefix (see
# routers/experiments.py:_dataset_s3_key). Write endpoints are scoped to it so
# a caller can't clobber arbitrary objects in the co-located store.
_PROJECT_KEY_PREFIX = "datasets/projects/"

# 8 MB: bounded memory per in-flight upload, above S3's 5 MB multipart
# minimum part size.
_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


def _validate_bucket(bucket: str) -> None:
    """Only the buckets the app provisions are addressable."""
    if bucket not in settings.s3_allowed_buckets:
        raise HTTPException(status_code=400, detail=f"Unknown bucket: {bucket!r}")


def _validate_key(key: str, *, for_write: bool = False) -> None:
    """Reject keys that are absolute, contain traversal/backslash segments, or
    (for write endpoints) fall outside the app's project-data prefix."""
    if not key or len(key) > 1024 or "\\" in key:
        raise HTTPException(status_code=400, detail=f"Invalid S3 key: {key!r}")
    if any(part in ("", ".", "..") for part in key.split("/")):
        raise HTTPException(status_code=400, detail=f"Invalid S3 key: {key!r}")
    if for_write and not key.startswith(_PROJECT_KEY_PREFIX):
        raise HTTPException(
            status_code=400,
            detail=f"Writes must target the {_PROJECT_KEY_PREFIX!r} prefix",
        )


class PresignRequest(BaseModel):
    bucket: str
    key: str
    expires_in: int = 3600


@router.get("/buckets")
async def list_buckets():
    try:
        response = await asyncio.to_thread(get_s3_client().list_buckets)
        buckets = [b["Name"] for b in response.get("Buckets", [])]
        return {"buckets": buckets}
    except Exception as e:
        logger.error(f"S3 list_buckets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_objects(bucket: str, prefix: Optional[str] = ""):
    try:
        params = {"Bucket": bucket, "Delimiter": "/"}
        if prefix:
            params["Prefix"] = prefix

        response = await asyncio.to_thread(get_s3_client().list_objects_v2, **params)

        folders = [
            {"name": p["Prefix"].rstrip("/").split("/")[-1], "prefix": p["Prefix"]}
            for p in response.get("CommonPrefixes", [])
            if not should_ignore_workspace_path(p["Prefix"])
        ]
        files = [
            {
                "name": obj["Key"].split("/")[-1],
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            }
            for obj in response.get("Contents", [])
            if obj["Key"] != prefix and not should_ignore_workspace_path(obj["Key"])
        ]

        return {"bucket": bucket, "prefix": prefix, "folders": folders, "files": files}
    except Exception as e:
        logger.error(f"S3 list_objects: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/presign")
async def generate_presigned_url(req: PresignRequest):
    _validate_bucket(req.bucket)
    _validate_key(req.key, for_write=True)
    try:
        url = await asyncio.to_thread(
            get_s3_client().generate_presigned_url,
            "put_object",
            Params={"Bucket": req.bucket, "Key": req.key},
            ExpiresIn=req.expires_in,
        )
        # Replace internal endpoint with external one for browser access
        internal = settings.s3_endpoint
        external = get_s3_external_endpoint()
        url = url.replace(internal, external)
        return {"url": url, "bucket": req.bucket, "key": req.key}
    except Exception as e:
        logger.error(f"S3 presign: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    bucket: str, key: str, file: UploadFile = File(...)
) -> UploadResponse:
    # NOTE: authentication for this router is handled globally (issue #88);
    # this endpoint only enforces target validation and bounded streaming.
    _validate_bucket(bucket)
    _validate_key(key, for_write=True)

    s3 = get_s3_client()
    content_type = file.content_type or "application/octet-stream"
    max_bytes = settings.max_upload_size_bytes
    total = 0

    async def _read_chunk() -> bytes:
        nonlocal total
        data = await file.read(_UPLOAD_CHUNK_BYTES)
        total += len(data)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds max upload size of "
                f"{max_bytes // (1024 * 1024)}MB",
            )
        return data

    try:
        chunk = await _read_chunk()
        next_chunk = await _read_chunk()

        if not next_chunk:
            # Fits in a single bounded chunk — plain put_object.
            await asyncio.to_thread(
                s3.put_object,
                Bucket=bucket,
                Key=key,
                Body=chunk,
                ContentType=content_type,
            )
            return UploadResponse(bucket=bucket, key=key, size=total)

        # Larger body: stream through a multipart upload so we never hold
        # more than two chunks in memory.
        mpu = await asyncio.to_thread(
            s3.create_multipart_upload, Bucket=bucket, Key=key, ContentType=content_type
        )
        upload_id = mpu["UploadId"]
        try:
            parts = []
            part_number = 1
            while chunk:
                part = await asyncio.to_thread(
                    s3.upload_part,
                    Bucket=bucket,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=chunk,
                )
                parts.append({"ETag": part["ETag"], "PartNumber": part_number})
                part_number += 1
                chunk = next_chunk
                next_chunk = await _read_chunk() if chunk else b""
            await asyncio.to_thread(
                s3.complete_multipart_upload,
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except BaseException:
            try:
                # Shielded so task cancellation (e.g. client disconnect) can't
                # cancel the abort before the worker thread picks it up, which
                # would orphan the multipart upload until S3's TTL clears it.
                await asyncio.shield(
                    asyncio.to_thread(
                        s3.abort_multipart_upload,
                        Bucket=bucket,
                        Key=key,
                        UploadId=upload_id,
                    )
                )
            except Exception as abort_err:  # pragma: no cover - best effort
                logger.warning(f"S3 abort_multipart_upload: {abort_err}")
            raise
        return UploadResponse(bucket=bucket, key=key, size=total)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"S3 upload: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download")
async def get_download_url(bucket: str, key: str):
    _validate_bucket(bucket)
    _validate_key(key)
    try:
        url = await asyncio.to_thread(
            get_s3_client().generate_presigned_url,
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,
        )
        internal = settings.s3_endpoint
        external = get_s3_external_endpoint()
        url = url.replace(internal, external)
        return {"url": url, "bucket": bucket, "key": key}
    except Exception as e:
        logger.error(f"S3 download: {e}")
        raise HTTPException(status_code=500, detail=str(e))
