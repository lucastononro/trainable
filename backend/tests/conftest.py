"""Shared test fixtures."""

import asyncio
import io
import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Use in-memory SQLite for tests (no Postgres needed)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

# Mock claude_agent_sdk if it's not installed (it's a private package)
if "claude_agent_sdk" not in sys.modules:
    _mock_sdk = types.ModuleType("claude_agent_sdk")
    _mock_sdk.query = AsyncMock()
    _mock_sdk.ClaudeAgentOptions = MagicMock
    _mock_sdk.AssistantMessage = type("AssistantMessage", (), {})
    _mock_sdk.ResultMessage = type("ResultMessage", (), {})
    _mock_sdk.SystemMessage = type("SystemMessage", (), {})
    _mock_sdk.UserMessage = type("UserMessage", (), {})
    sys.modules["claude_agent_sdk"] = _mock_sdk

# Mock mcp package if it's not installed
if "mcp" not in sys.modules:
    _mock_mcp = types.ModuleType("mcp")
    _mock_mcp_server = types.ModuleType("mcp.server")
    _mock_mcp_server_ll = types.ModuleType("mcp.server.lowlevel")
    _mock_mcp_types = types.ModuleType("mcp.types")
    _mock_mcp_server_ll.Server = MagicMock
    _mock_mcp_types.TextContent = MagicMock
    _mock_mcp_types.Tool = MagicMock
    _mock_mcp_types.CallToolResult = MagicMock
    sys.modules["mcp"] = _mock_mcp
    sys.modules["mcp.server"] = _mock_mcp_server
    sys.modules["mcp.server.lowlevel"] = _mock_mcp_server_ll
    sys.modules["mcp.types"] = _mock_mcp_types

from db import Base, engine
from main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create fresh tables for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    """Async test client with S3 mocked."""
    transport = ASGITransport(app=app)
    with (
        patch("services.s3_client._client", None),
        patch("services.s3_client.get_s3_client") as mock_s3,
        patch("routers.experiments.get_s3_client") as mock_s3_exp,
        patch("routers.experiments.upload_to_volume", new_callable=AsyncMock),
        patch("main._init_s3_buckets"),
    ):
        mock_client = MagicMock()
        mock_client.put_object = MagicMock()
        # Mock get_object to return bytes for from-s3 endpoint
        mock_body = MagicMock()
        mock_body.read.return_value = b"col1,col2\n1,2\n"
        mock_client.get_object.return_value = {"Body": mock_body}
        mock_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "my-data/raw.csv"}]
        }
        mock_s3.return_value = mock_client
        mock_s3_exp.return_value = mock_client
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV file."""
    csv_path = tmp_path / "iris.csv"
    csv_path.write_text(
        "sepal_length,sepal_width,petal_length,petal_width,species\n"
        "5.1,3.5,1.4,0.2,setosa\n"
        "4.9,3.0,1.4,0.2,setosa\n"
        "7.0,3.2,4.7,1.4,versicolor\n"
        "6.3,3.3,6.0,2.5,virginica\n"
    )
    return csv_path


@pytest.fixture
def sample_folder(tmp_path):
    """Create a sample folder with multiple files."""
    data_dir = tmp_path / "dataset"
    data_dir.mkdir()
    (data_dir / "train.csv").write_text("x,y\n1,2\n3,4\n")
    (data_dir / "test.csv").write_text("x,y\n5,6\n7,8\n")
    (data_dir / "metadata.json").write_text('{"target": "y"}')
    return data_dir


# ---------------------------------------------------------------------------
# Parquet fixtures for data prep tests
# ---------------------------------------------------------------------------


def _make_parquet_bytes(data: dict, num_rows=None) -> bytes:
    """Create parquet bytes from a column dict. Uses pyarrow directly."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    arrays = []
    names = []
    for col_name, values in data.items():
        names.append(col_name)
        arrays.append(pa.array(values))

    table = pa.table(dict(zip(names, arrays)))
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


@pytest.fixture
def sample_parquet_splits():
    """Create train/val/test parquet bytes with consistent schema."""
    train_data = {
        "feature_a": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        "feature_b": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0],
        "target": [0, 1, 0, 1, 0, 1, 0],
    }
    val_data = {
        "feature_a": [8.0, 9.0],
        "feature_b": [80.0, 90.0],
        "target": [1, 0],
    }
    test_data = {
        "feature_a": [10.0, 11.0],
        "feature_b": [100.0, 110.0],
        "target": [0, 1],
    }
    return {
        "train": _make_parquet_bytes(train_data),
        "val": _make_parquet_bytes(val_data),
        "test": _make_parquet_bytes(test_data),
    }


@pytest.fixture
def sample_metadata_json():
    """Create sample metadata.json content as bytes."""
    meta = {
        "target_column": "target",
        "problem_type": "classification",
        "features": ["feature_a", "feature_b"],
        "categorical_features": [],
        "numeric_features": ["feature_a", "feature_b"],
        "n_classes": 2,
        "class_distribution": {"0": 0.55, "1": 0.45},
        "splits": {"train": {"rows": 7}, "val": {"rows": 2}, "test": {"rows": 2}},
        "transforms": {"scaler": "StandardScaler"},
        "random_seed": 42,
        "original_shape": [11, 3],
    }
    return json.dumps(meta).encode("utf-8")


class MockVolumeEntry:
    """Mimics a Modal Volume directory entry."""

    def __init__(self, path: str, is_file: bool = True):
        self.path = path
        self.type = SimpleNamespace(name="FILE" if is_file else "DIRECTORY")


class MockVolume:
    """Mock Modal Volume backed by an in-memory file dict."""

    def __init__(self, files: dict[str, bytes]):
        self._files = files

    def reload(self):
        pass

    def read_file(self, path: str):
        if path in self._files:
            return [self._files[path]]
        raise FileNotFoundError(f"Mock volume: {path} not found")

    def listdir(self, prefix: str, recursive: bool = False):
        entries = []
        for path in self._files:
            if path.startswith(prefix + "/") or path == prefix:
                entries.append(MockVolumeEntry(path, is_file=True))
        return entries


@pytest.fixture
def mock_volume_with_prep(sample_parquet_splits, sample_metadata_json):
    """MockVolume pre-loaded with data-prep outputs in the flat session layout
    used by the multi-agent workspace (no /prep/ subfolder)."""
    files = {
        "/sessions/test-session/data/train.parquet": sample_parquet_splits["train"],
        "/sessions/test-session/data/val.parquet": sample_parquet_splits["val"],
        "/sessions/test-session/data/test.parquet": sample_parquet_splits["test"],
        "/sessions/test-session/data/metadata.json": sample_metadata_json,
        "/sessions/test-session/report.md": b"# Prep Report\nAll good.",
        "/sessions/test-session/scripts/step_01_clean.py": b"import pandas as pd\n",
        "/datasets/test-experiment/iris.csv": b"a,b,target\n1,2,0\n",
    }
    return MockVolume(files)


@pytest.fixture
def mock_volume_with_train(sample_parquet_splits):
    """MockVolume pre-loaded with trainer outputs in the flat session layout."""
    train_meta = json.dumps(
        {
            "best_model": "XGBClassifier",
            "best_model_params": {"n_estimators": 100},
            "test_metrics": {"accuracy": 0.91, "f1_weighted": 0.90},
            "feature_importance": {"feature_a": 0.6, "feature_b": 0.4},
        }
    ).encode("utf-8")
    files = {
        "/sessions/test-session/models/model.pkl": b"fake-model-bytes",
        "/sessions/test-session/report.md": b"# Train Report\nBest model: XGB.",
        "/sessions/test-session/data/metadata.json": train_meta,
        "/sessions/test-session/figures/confusion_matrix.png": b"fake-png",
    }
    return MockVolume(files)
