"""S3 browser endpoints for navigating external S3 buckets."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from config import settings
from services.s3_client import get_s3_client, get_s3_external_endpoint
from services.volume import should_ignore_workspace_path

logger = logging.getLogger(__name__)
router = APIRouter()


class PresignRequest(BaseModel):
    bucket: str
    key: str
    expires_in: int = 3600


@router.get("/buckets")
async def list_buckets():
    try:
        response = get_s3_client().list_buckets()
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

        response = get_s3_client().list_objects_v2(**params)

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
            if obj["Key"] != prefix
            and not should_ignore_workspace_path(obj["Key"])
        ]

        return {"bucket": bucket, "prefix": prefix, "folders": folders, "files": files}
    except Exception as e:
        logger.error(f"S3 list_objects: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/presign")
async def generate_presigned_url(req: PresignRequest):
    try:
        url = get_s3_client().generate_presigned_url(
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


@router.post("/upload")
async def upload_file(bucket: str, key: str, file: UploadFile = File(...)):
    try:
        content = await file.read()
        get_s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType=file.content_type or "application/octet-stream",
        )
        return {
            "status": "uploaded",
            "bucket": bucket,
            "key": key,
            "size": len(content),
        }
    except Exception as e:
        logger.error(f"S3 upload: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download")
async def get_download_url(bucket: str, key: str):
    try:
        url = get_s3_client().generate_presigned_url(
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
