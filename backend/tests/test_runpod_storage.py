"""Tests for the RunPod storage backend — S3 key mapping, FileEntry
normalization, and Modal-compatible listdir/read semantics. boto3 is
replaced by an in-memory fake."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from services.compute.base import FileEntryType
from services.compute.runpod_provider.storage import RunPodStorage, _key


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        prefix = kwargs.get("Prefix", "")
        delimiter = kwargs.get("Delimiter")
        return self._pages(prefix, delimiter)


class FakeS3:
    """Minimal in-memory S3: objects dict key -> bytes."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

        class ClientError(Exception):
            def __init__(self, response=None):
                super().__init__("client error")
                self.response = response or {}

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey()
        import io

        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def upload_file(self, Filename, Bucket, Key, Config=None):
        with open(Filename, "rb") as f:
            self.objects[Key] = f.read()

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.objects.pop(obj["Key"], None)

    def get_paginator(self, name):
        assert name == "list_objects_v2"

        def pages(prefix, delimiter):
            keys = sorted(k for k in self.objects if k.startswith(prefix))
            if delimiter:
                contents, prefixes = [], []
                seen = set()
                for k in keys:
                    rest = k[len(prefix) :]
                    if delimiter in rest:
                        p = prefix + rest.split(delimiter, 1)[0] + delimiter
                        if p not in seen:
                            seen.add(p)
                            prefixes.append({"Prefix": p})
                    else:
                        contents.append(self._obj(k))
                return [
                    {
                        "Contents": contents,
                        "CommonPrefixes": prefixes,
                    }
                ]
            return [{"Contents": [self._obj(k) for k in keys]}]

        return FakePaginator(pages)

    def _obj(self, key):
        return {
            "Key": key,
            "Size": len(self.objects[key]),
            "LastModified": datetime.now(timezone.utc),
        }


@pytest.fixture
def storage(monkeypatch):
    s = RunPodStorage()
    fake = FakeS3()
    monkeypatch.setattr(s, "_s3", lambda: fake)
    monkeypatch.setattr(s, "_bucket", AsyncMock(return_value="vol-1"))
    s._fake = fake
    return s


class TestKeyMapping:
    def test_leading_slash_stripped(self):
        assert _key("/sessions/s1/data.csv") == "sessions/s1/data.csv"
        assert _key("sessions/s1/data.csv") == "sessions/s1/data.csv"


class TestReadWrite:
    @pytest.mark.asyncio
    async def test_write_then_read_roundtrip(self, storage):
        await storage.write("hello", "/sessions/s1/a.txt")
        assert await storage.read_file("/sessions/s1/a.txt") == b"hello"
        # bytes path
        await storage.write(b"\x00\x01", "/sessions/s1/b.bin")
        assert await storage.read_file("sessions/s1/b.bin") == b"\x00\x01"

    @pytest.mark.asyncio
    async def test_missing_file_raises_file_not_found(self, storage):
        with pytest.raises(FileNotFoundError):
            await storage.read_file("/sessions/s1/missing.txt")

    @pytest.mark.asyncio
    async def test_ensure_session_workspace_lays_down_init(self, storage):
        await storage.ensure_session_workspace("sess-9")
        assert "sessions/sess-9/src/__init__.py" in storage._fake.objects


class TestListdir:
    @pytest.mark.asyncio
    async def test_recursive_lists_files_and_synthesizes_dirs(self, storage):
        await storage.write("a", "/sessions/s1/data/train.csv")
        await storage.write("b", "/sessions/s1/report.md")
        entries = await storage.listdir("/sessions/s1", recursive=True)

        by_path = {e.path: e for e in entries}
        # Paths have no leading slash — mirroring modal.Volume.listdir.
        assert "sessions/s1/data/train.csv" in by_path
        assert by_path["sessions/s1/data/train.csv"].type.name == "FILE"
        assert by_path["sessions/s1/data"].type == FileEntryType.DIRECTORY
        assert by_path["sessions/s1/report.md"].size == 1

    @pytest.mark.asyncio
    async def test_non_recursive_uses_delimiter(self, storage):
        await storage.write("a", "/sessions/s1/data/train.csv")
        await storage.write("b", "/sessions/s1/report.md")
        entries = await storage.listdir("/sessions/s1", recursive=False)
        by_path = {e.path: e.type.name for e in entries}
        assert by_path == {
            "sessions/s1/data": "DIRECTORY",
            "sessions/s1/report.md": "FILE",
        }

    @pytest.mark.asyncio
    async def test_missing_directory_raises(self, storage):
        with pytest.raises(FileNotFoundError):
            await storage.listdir("/sessions/nope", recursive=True)


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_prefix_recursive(self, storage):
        await storage.write("a", "/sessions/s1/data/train.csv")
        await storage.write("b", "/sessions/s1/data/test.csv")
        await storage.write("c", "/sessions/s1/keep.md")
        await storage.remove("/sessions/s1/data")
        assert list(storage._fake.objects) == ["sessions/s1/keep.md"]


class TestReload:
    @pytest.mark.asyncio
    async def test_reload_is_live_noop(self, storage):
        assert await storage.reload() is True
        assert storage.reload_sync() is True
