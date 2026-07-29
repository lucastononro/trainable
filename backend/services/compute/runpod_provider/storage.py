"""RunPod network-volume implementation of StorageBackend.

The network volume mounts live into pods (/data) and serverless workers
(/runpod-volume → symlinked to /data by the worker entrypoint), and the
backend reads/writes it out-of-band through RunPod's S3-compatible API
(`https://s3api-{DC}.runpod.io`, bucket = network volume id) — exactly the
role modal.Volume's client API plays for the Modal provider.

Path convention matches Modal: inputs may carry a leading slash
(`/sessions/{sid}/x`); S3 keys never do. `listdir` returns FileEntry
objects whose `.path` has no leading slash, mirroring Modal's entries.

Quirks handled here: no presigned URLs (unused), 500 MB single-PUT cap
(multipart via TransferConfig), no batch primitive (bounded-concurrency
thread pool for upload_many).
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from config import settings
from services.compute.base import FileEntry, FileEntryType, StorageBackend

logger = logging.getLogger(__name__)

_UPLOAD_CONCURRENCY = 8


def _key(path: str) -> str:
    return path.lstrip("/")


class RunPodStorage(StorageBackend):
    name = "runpod"

    def __init__(self):
        self._client = None
        self._transfer_config = None

    def _s3(self):
        if self._client is None:
            import boto3
            from boto3.s3.transfer import TransferConfig
            from botocore.config import Config

            dc = settings.runpod_datacenter_id
            self._client = boto3.client(
                "s3",
                endpoint_url=f"https://s3api-{dc.lower()}.runpod.io",
                aws_access_key_id=settings.runpod_s3_access_key_id,
                aws_secret_access_key=settings.runpod_s3_secret_access_key,
                region_name=dc,
                config=Config(
                    retries={"max_attempts": 3, "mode": "standard"},
                    s3={"addressing_style": "path"},
                ),
            )
            # RunPod's S3 API caps single PUTs at 500 MB — multipart well
            # below that.
            self._transfer_config = TransferConfig(
                multipart_threshold=256 * 1024 * 1024,
                multipart_chunksize=128 * 1024 * 1024,
            )
        return self._client

    async def _bucket(self) -> str:
        from services.compute.runpod_provider.bootstrap import ensure_network_volume

        return await ensure_network_volume()

    async def _run(self, fn):
        return await asyncio.get_running_loop().run_in_executor(None, fn)

    async def read_file(self, path: str) -> bytes:
        bucket = await self._bucket()
        s3 = self._s3()
        key = _key(path)

        def _sync() -> bytes:
            try:
                resp = s3.get_object(Bucket=bucket, Key=key)
                return resp["Body"].read()
            except s3.exceptions.NoSuchKey:
                raise FileNotFoundError(path)
            except s3.exceptions.ClientError as e:
                code = (e.response.get("Error") or {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    raise FileNotFoundError(path)
                raise

        return await self._run(_sync)

    async def listdir(self, path: str, recursive: bool = False) -> list:
        bucket = await self._bucket()
        s3 = self._s3()
        prefix = _key(path).rstrip("/")
        prefix = f"{prefix}/" if prefix else ""

        def _sync() -> list:
            entries: list[FileEntry] = []
            seen_dirs: set[str] = set()
            paginator = s3.get_paginator("list_objects_v2")
            kwargs = {"Bucket": bucket, "Prefix": prefix}
            if not recursive:
                kwargs["Delimiter"] = "/"
            found_any = False
            for page in paginator.paginate(**kwargs):
                for cp in page.get("CommonPrefixes") or []:
                    found_any = True
                    dir_key = cp["Prefix"].rstrip("/")
                    if dir_key not in seen_dirs:
                        seen_dirs.add(dir_key)
                        entries.append(
                            FileEntry(path=dir_key, type=FileEntryType.DIRECTORY)
                        )
                for obj in page.get("Contents") or []:
                    found_any = True
                    obj_key = obj["Key"]
                    if obj_key.endswith("/"):
                        continue
                    entries.append(
                        FileEntry(
                            path=obj_key,
                            type=FileEntryType.FILE,
                            size=obj.get("Size"),
                            mtime=(
                                obj["LastModified"].timestamp()
                                if obj.get("LastModified")
                                else None
                            ),
                        )
                    )
                    if recursive:
                        # Synthesize intermediate directory entries so tree
                        # builders see the same shape Modal produces.
                        parent = obj_key.rsplit("/", 1)[0]
                        while parent and parent != prefix.rstrip("/"):
                            if parent in seen_dirs:
                                break
                            seen_dirs.add(parent)
                            entries.append(
                                FileEntry(path=parent, type=FileEntryType.DIRECTORY)
                            )
                            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
            if not found_any and prefix:
                # Modal raises FileNotFoundError for a missing directory;
                # callers (file tree route, snapshot) rely on that.
                raise FileNotFoundError(path)
            return entries

        return await self._run(_sync)

    async def upload(self, local_path: str, remote_path: str) -> None:
        bucket = await self._bucket()
        s3 = self._s3()

        def _sync():
            s3.upload_file(
                local_path, bucket, _key(remote_path), Config=self._transfer_config
            )

        await self._run(_sync)

    async def upload_many(self, pairs: list[tuple[str, str]]) -> int:
        if not pairs:
            return 0
        bucket = await self._bucket()
        s3 = self._s3()

        def _sync() -> int:
            # No batch primitive on the S3 API — bounded concurrency
            # substitutes for Modal's single-round-trip batch_upload.
            with ThreadPoolExecutor(max_workers=_UPLOAD_CONCURRENCY) as pool:
                futures = [
                    pool.submit(
                        s3.upload_file,
                        local,
                        bucket,
                        _key(remote),
                        Config=self._transfer_config,
                    )
                    for local, remote in pairs
                ]
                for f in futures:
                    f.result()
            return len(pairs)

        return await self._run(_sync)

    async def remove(self, path: str) -> None:
        bucket = await self._bucket()
        s3 = self._s3()
        key = _key(path)

        def _sync():
            # `path` may be a file or a directory — delete the exact key
            # plus everything under it (Modal's remove_file(recursive=True)
            # semantics).
            deleted = False
            try:
                s3.delete_object(Bucket=bucket, Key=key)
                deleted = True
            except Exception:
                pass
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=f"{key}/"):
                contents = page.get("Contents") or []
                if not contents:
                    continue
                deleted = True
                s3.delete_objects(
                    Bucket=bucket,
                    Delete={
                        "Objects": [{"Key": o["Key"]} for o in contents],
                        "Quiet": True,
                    },
                )
            if not deleted:
                logger.debug("[runpod] remove: nothing at %s", path)

        await self._run(_sync)

    async def write(self, content: str | bytes, remote_path: str) -> None:
        bucket = await self._bucket()
        s3 = self._s3()
        body = content.encode("utf-8") if isinstance(content, str) else bytes(content)

        def _sync():
            s3.put_object(Bucket=bucket, Key=_key(remote_path), Body=body)

        await self._run(_sync)

    async def ensure_session_workspace(self, session_id: str) -> None:
        try:
            await self.write("", f"/sessions/{session_id}/src/__init__.py")
        except Exception as e:
            logger.debug("ensure_session_workspace skipped: %s", e)

    async def reload(self) -> bool:
        # S3 reads are always live — nothing to refresh.
        return True

    def reload_sync(self) -> bool:
        return True

    def reset(self) -> None:
        """Test hook — drop the cached boto3 client."""
        self._client = None
        self._transfer_config = None
