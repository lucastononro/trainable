"""S3 browser upload endpoint — bucket/key validation and bounded streaming."""

from unittest.mock import MagicMock, patch

import pytest

from routers import s3_browser


@pytest.fixture
def mock_s3():
    """Patch the boto3 client used by the s3_browser router."""
    client = MagicMock()
    client.create_multipart_upload.return_value = {"UploadId": "test-upload-id"}
    client.upload_part.return_value = {"ETag": '"etag"'}
    with patch("routers.s3_browser.get_s3_client", return_value=client):
        yield client


@pytest.mark.asyncio
async def test_upload_small_file_ok(client, mock_s3):
    resp = await client.post(
        "/api/s3/upload",
        params={"bucket": "datasets", "key": "datasets/projects/p1/train.csv"},
        files={"file": ("train.csv", b"x,y\n1,2\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "uploaded"
    assert body["size"] == len(b"x,y\n1,2\n")
    mock_s3.put_object.assert_called_once()
    assert mock_s3.put_object.call_args.kwargs["Bucket"] == "datasets"
    assert (
        mock_s3.put_object.call_args.kwargs["Key"] == "datasets/projects/p1/train.csv"
    )
    mock_s3.create_multipart_upload.assert_not_called()


@pytest.mark.asyncio
async def test_upload_response_is_typed(client, mock_s3):
    """The upload response follows the UploadResponse schema and the endpoint
    declares it in OpenAPI (routers/AGENTS.md: no raw-dict responses)."""
    resp = await client.post(
        "/api/s3/upload",
        params={"bucket": "datasets", "key": "datasets/projects/p1/train.csv"},
        files={"file": ("train.csv", b"x,y\n1,2\n", "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"status", "bucket", "key", "size"}
    assert body == {
        "status": "uploaded",
        "bucket": "datasets",
        "key": "datasets/projects/p1/train.csv",
        "size": len(b"x,y\n1,2\n"),
    }

    spec = (await client.get("/openapi.json")).json()
    assert "UploadResponse" in spec["components"]["schemas"]
    upload_op = spec["paths"]["/api/s3/upload"]["post"]
    ok_schema = upload_op["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok_schema["$ref"].endswith("/UploadResponse")


@pytest.mark.asyncio
async def test_upload_unknown_bucket_rejected(client, mock_s3):
    resp = await client.post(
        "/api/s3/upload",
        params={"bucket": "someone-elses-bucket", "key": "datasets/projects/p1/x"},
        files={"file": ("x", b"data", "application/octet-stream")},
    )
    assert resp.status_code == 400
    mock_s3.put_object.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_key",
    [
        "datasets/projects/../secrets",
        "/etc/passwd",
        "datasets/projects/p1/..\\..\\x",
        "outside/the/project/prefix",
        "",
    ],
)
async def test_upload_bad_key_rejected(client, mock_s3, bad_key):
    resp = await client.post(
        "/api/s3/upload",
        params={"bucket": "datasets", "key": bad_key},
        files={"file": ("x", b"data", "application/octet-stream")},
    )
    assert resp.status_code == 400
    mock_s3.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_upload_oversize_rejected_without_buffering(client, mock_s3, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "max_upload_size_bytes", 16)
    resp = await client.post(
        "/api/s3/upload",
        params={"bucket": "datasets", "key": "datasets/projects/p1/big.bin"},
        files={"file": ("big.bin", b"z" * 64, "application/octet-stream")},
    )
    assert resp.status_code == 413
    mock_s3.put_object.assert_not_called()
    mock_s3.complete_multipart_upload.assert_not_called()


@pytest.mark.asyncio
async def test_upload_large_file_streams_multipart(client, mock_s3, monkeypatch):
    # Shrink the chunk size so a few KB exercises the multipart path.
    monkeypatch.setattr(s3_browser, "_UPLOAD_CHUNK_BYTES", 1024)
    payload = b"a" * (3 * 1024 + 100)
    resp = await client.post(
        "/api/s3/upload",
        params={"bucket": "datasets", "key": "datasets/projects/p1/big.bin"},
        files={"file": ("big.bin", payload, "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["size"] == len(payload)
    mock_s3.put_object.assert_not_called()
    mock_s3.create_multipart_upload.assert_called_once()
    assert mock_s3.upload_part.call_count == 4
    mock_s3.complete_multipart_upload.assert_called_once()
    mock_s3.abort_multipart_upload.assert_not_called()


@pytest.mark.asyncio
async def test_upload_oversize_mid_multipart_aborts(client, mock_s3, monkeypatch):
    from config import settings

    monkeypatch.setattr(s3_browser, "_UPLOAD_CHUNK_BYTES", 1024)
    monkeypatch.setattr(settings, "max_upload_size_bytes", 2048)
    resp = await client.post(
        "/api/s3/upload",
        params={"bucket": "datasets", "key": "datasets/projects/p1/big.bin"},
        files={"file": ("big.bin", b"a" * 4096, "application/octet-stream")},
    )
    assert resp.status_code == 413
    mock_s3.abort_multipart_upload.assert_called_once()
    mock_s3.complete_multipart_upload.assert_not_called()


@pytest.mark.asyncio
async def test_presign_validates_bucket_and_key(client, mock_s3):
    resp = await client.post(
        "/api/s3/presign",
        json={"bucket": "evil", "key": "datasets/projects/p1/x"},
    )
    assert resp.status_code == 400

    resp = await client.post(
        "/api/s3/presign",
        json={"bucket": "datasets", "key": "../../x"},
    )
    assert resp.status_code == 400
    mock_s3.generate_presigned_url.assert_not_called()


@pytest.mark.asyncio
async def test_download_validates_bucket_and_key(client, mock_s3):
    resp = await client.get(
        "/api/s3/download", params={"bucket": "evil", "key": "some/key"}
    )
    assert resp.status_code == 400

    resp = await client.get(
        "/api/s3/download", params={"bucket": "datasets", "key": "a/../b"}
    )
    assert resp.status_code == 400
    mock_s3.generate_presigned_url.assert_not_called()
