"""Unit tests for services/sandbox.py — run_code's Modal Sandbox lifecycle.

`modal.Sandbox` itself is faked end-to-end (create/stdout/stderr/wait); every
other collaborator (metrics dispatch, usage recording, span attributes) is
exercised through the real `run_code` control flow so we're asserting on
actual routing/attribute-setting logic, not a mock calling a mock.

Three paths per the issue:
  - success:   returncode == 0, usage recorded is_error=False, span clean.
  - "timeout": Modal kills the sandbox and returns a non-zero returncode
    (this is how a per-call timeout actually surfaces here — run_code never
    raises on its own for this case, it just reports the failure).
  - exception: sandbox creation itself blows up (e.g. Modal API error) —
    run_code must tag the span with error=True and re-raise.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import modal
import pytest


class _FakeStream:
    """Mimics Modal's async stdout/stderr stream."""

    def __init__(self, chunks: list[str]):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c
            # Real Modal streams cede control to the event loop between
            # chunks (network I/O). A bare synchronous generator would let
            # the stdout loop race all the way to completion — and cancel
            # the concurrently-scheduled stderr drain task — before that
            # task ever gets a turn to run. Ceding control here after each
            # yield keeps the interleaving realistic.
            await asyncio.sleep(0)


class _FakeSandbox:
    def __init__(
        self, stdout_chunks: list[str], stderr_chunks: list[str], returncode: int = 0
    ):
        self.stdout = _FakeStream(stdout_chunks)
        self.stderr = _FakeStream(stderr_chunks)
        self.returncode = returncode
        self.wait = SimpleNamespace(aio=AsyncMock(return_value=None))


class _FakeSpanCtx:
    """Fake context manager standing in for `sandbox_span(...)`; records the
    span object so tests can assert on set_attribute calls after the fact."""

    def __init__(self):
        self.span = MagicMock()
        self.exc_type = None

    def __enter__(self):
        return self.span

    def __exit__(self, exc_type, exc, tb):
        self.exc_type = exc_type
        return False  # never swallow


@pytest.fixture
def fake_span(monkeypatch):
    """Patch services.sandbox.sandbox_span with a recorder and hand back the
    holder dict so the test can inspect the span used for the call."""
    import services.sandbox as sandbox_module

    holder: dict[str, _FakeSpanCtx] = {}

    def _fake_sandbox_span(**kwargs):
        ctx = _FakeSpanCtx()
        ctx.kwargs = kwargs
        holder["ctx"] = ctx
        return ctx

    monkeypatch.setattr(sandbox_module, "sandbox_span", _fake_sandbox_span)
    return holder


@pytest.fixture
def patched_sandbox_deps(monkeypatch):
    """Stub every Modal/volume/usage collaborator around run_code except
    modal.Sandbox itself, which each test configures directly."""
    import services.sandbox as sandbox_module
    import services.volume as volume_module

    monkeypatch.setattr(sandbox_module, "_get_app", AsyncMock(return_value=object()))
    monkeypatch.setattr(sandbox_module, "_get_image", MagicMock(return_value=object()))
    monkeypatch.setattr(sandbox_module, "get_volume", MagicMock(return_value=object()))

    # These are imported locally inside run_code (`from services.volume import
    # ensure_session_workspace as _ensure_ws, ...`), so patch the source
    # module's attributes — the lazy import re-resolves them at call time.
    monkeypatch.setattr(
        volume_module, "ensure_session_workspace", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        volume_module, "reload_volume_async", AsyncMock(return_value=True)
    )

    usage_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(sandbox_module, "record_sandbox_usage", usage_mock)

    dispatch_mocks = {
        "persist_and_publish": AsyncMock(return_value=None),
        "persist_and_publish_log_event": AsyncMock(return_value=None),
        "publish_chart_config": AsyncMock(return_value=None),
    }
    for name, mock in dispatch_mocks.items():
        monkeypatch.setattr(sandbox_module, name, mock)

    return SimpleNamespace(usage=usage_mock, dispatch=dispatch_mocks)


def _patch_create(monkeypatch, fake_sb: _FakeSandbox | Exception):
    """Patch modal.Sandbox.create.aio to return fake_sb, or raise it if it's
    an exception instance."""
    if isinstance(fake_sb, Exception):
        create_aio = AsyncMock(side_effect=fake_sb)
    else:
        create_aio = AsyncMock(return_value=fake_sb)
    monkeypatch.setattr(modal.Sandbox, "create", SimpleNamespace(aio=create_aio))
    return create_aio


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_code_success_returns_output_and_records_clean_usage(
    patched_sandbox_deps, fake_span, monkeypatch
):
    from services.sandbox import run_code

    fake_sb = _FakeSandbox(
        stdout_chunks=["hello\n", "world\n"], stderr_chunks=[], returncode=0
    )
    create_aio = _patch_create(monkeypatch, fake_sb)

    result = await run_code(
        code="print('hello')",
        session_id="sess-1",
        stage="eda",
        gpu=None,
        agent_type="eda",
        agent_id="root",
    )

    assert result == {"stdout": "hello\nworld\n", "stderr": "", "returncode": 0}

    # Sandbox was created exactly once, with the code baked into the script.
    assert create_aio.await_count == 1
    _, _, _, code_arg = create_aio.call_args.args[:4]
    assert "print('hello')" in code_arg
    assert create_aio.call_args.kwargs["workdir"] == "/data/sessions/sess-1"

    # Usage recorded as a clean run.
    patched_sandbox_deps.usage.assert_awaited_once()
    usage_kwargs = patched_sandbox_deps.usage.call_args.kwargs
    assert usage_kwargs["session_id"] == "sess-1"
    assert usage_kwargs["is_error"] is False
    assert usage_kwargs["seconds"] >= 0

    # Span got the expected attributes and was never tagged as an error.
    span = fake_span["ctx"].span
    attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert attr_calls["sandbox.code_chars"] == len("print('hello')")
    assert attr_calls["sandbox.returncode"] == 0
    assert "sandbox.elapsed_s" in attr_calls
    assert "error" not in attr_calls
    assert fake_span["ctx"].exc_type is None


@pytest.mark.asyncio
async def test_run_code_streams_stdout_and_dispatches_metrics(
    patched_sandbox_deps, fake_span, monkeypatch
):
    """A stdout line matching the metrics JSON envelope must be parsed and
    routed to persist_and_publish; plain text lines must not."""
    from services.sandbox import run_code

    fake_sb = _FakeSandbox(
        stdout_chunks=[
            "not json\n",
            '{"step": 1, "metrics": {"acc": 0.9}}\n',
        ],
        stderr_chunks=[],
        returncode=0,
    )
    _patch_create(monkeypatch, fake_sb)

    published: list[dict] = []
    from services.broadcaster import broadcaster

    orig_publish = broadcaster.publish

    async def _capture(session_id, event):
        published.append(event)
        return await orig_publish(session_id, event)

    monkeypatch.setattr(broadcaster, "publish", _capture)

    await run_code(code="print(1)", session_id="sess-2", stage="eda")

    # stdout chunks were broadcast as code_output events, in order.
    code_output_texts = [
        e["data"]["text"] for e in published if e["type"] == "code_output"
    ]
    assert code_output_texts == [
        "not json\n",
        '{"step": 1, "metrics": {"acc": 0.9}}\n',
    ]

    # Only the JSON metrics line was dispatched to persist_and_publish.
    metrics_mock = patched_sandbox_deps.dispatch["persist_and_publish"]
    metrics_mock.assert_awaited_once()
    call_args = metrics_mock.call_args.args
    assert call_args[0] == "sess-2"
    assert call_args[1] == "eda"
    assert call_args[2] == [{"step": 1, "name": "acc", "value": 0.9, "run_tag": None}]

    # No stage -> no dispatch at all (regression guard for the `if stage:` gate).
    patched_sandbox_deps.dispatch["publish_chart_config"].assert_not_awaited()
    patched_sandbox_deps.dispatch["persist_and_publish_log_event"].assert_not_awaited()


@pytest.mark.asyncio
async def test_run_code_no_stage_skips_metric_parsing(
    patched_sandbox_deps, fake_span, monkeypatch
):
    """Without a stage, run_code must not even attempt to parse stdout for
    metrics/log events/chart_config."""
    from services.sandbox import run_code

    fake_sb = _FakeSandbox(
        stdout_chunks=['{"step": 1, "metrics": {"acc": 0.9}}\n'],
        stderr_chunks=[],
        returncode=0,
    )
    _patch_create(monkeypatch, fake_sb)

    await run_code(code="print(1)", session_id="sess-3", stage=None)

    patched_sandbox_deps.dispatch["persist_and_publish"].assert_not_awaited()


# ---------------------------------------------------------------------------
# "Timeout" path — Modal kills the container; sandbox surfaces a non-zero
# returncode rather than raising. run_code must still return normally but
# flag the run as an error for usage accounting and tracing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_code_nonzero_returncode_marks_error_but_does_not_raise(
    patched_sandbox_deps, fake_span, monkeypatch
):
    from services.sandbox import run_code

    fake_sb = _FakeSandbox(
        stdout_chunks=["partial output\n"],
        stderr_chunks=["Killed\n"],
        returncode=137,  # SIGKILL exit code — how Modal reports a timeout kill
    )
    _patch_create(monkeypatch, fake_sb)

    result = await run_code(
        code="while True: pass",
        session_id="sess-4",
        stage="train",
        timeout=5,
        agent_type="trainer",
    )

    assert result["returncode"] == 137
    assert result["stderr"] == "Killed\n"

    patched_sandbox_deps.usage.assert_awaited_once()
    usage_kwargs = patched_sandbox_deps.usage.call_args.kwargs
    assert usage_kwargs["is_error"] is True

    span = fake_span["ctx"].span
    attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert attr_calls["error"] is True
    assert attr_calls["sandbox.returncode"] == 137
    assert attr_calls["sandbox.timeout_s"] == 5
    assert fake_span["ctx"].exc_type is None  # no exception propagated


# ---------------------------------------------------------------------------
# Exception path — sandbox creation itself raises. run_code must tag the
# span with error=True and re-raise (never swallow).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_code_create_failure_tags_span_and_reraises(
    patched_sandbox_deps, fake_span, monkeypatch
):
    from services.sandbox import run_code

    boom = RuntimeError("modal API unavailable")
    _patch_create(monkeypatch, boom)

    with pytest.raises(RuntimeError, match="modal API unavailable"):
        await run_code(code="print(1)", session_id="sess-5", stage="eda")

    span = fake_span["ctx"].span
    attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert attr_calls.get("error") is True
    assert fake_span["ctx"].exc_type is RuntimeError

    # We never got far enough to record usage or a returncode.
    patched_sandbox_deps.usage.assert_not_awaited()
    assert "sandbox.returncode" not in attr_calls


@pytest.mark.asyncio
async def test_run_code_stdout_iteration_failure_tags_span_and_reraises(
    patched_sandbox_deps, fake_span, monkeypatch
):
    """A mid-stream failure (e.g. the sandbox connection dropping while
    reading stdout) must also be treated as an exception path: tag the span
    and propagate, not silently report a fake success."""
    from services.sandbox import run_code

    class _BoomStream:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            if False:
                yield  # pragma: no cover - makes this an async generator
            raise ConnectionError("stream dropped")

    fake_sb = _FakeSandbox(stdout_chunks=[], stderr_chunks=[], returncode=0)
    fake_sb.stdout = _BoomStream()
    _patch_create(monkeypatch, fake_sb)

    with pytest.raises(ConnectionError, match="stream dropped"):
        await run_code(code="print(1)", session_id="sess-6", stage="eda")

    span = fake_span["ctx"].span
    attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert attr_calls.get("error") is True
    assert fake_span["ctx"].exc_type is ConnectionError
    patched_sandbox_deps.usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_code_passes_gpu_and_timeout_through(
    patched_sandbox_deps, fake_span, monkeypatch
):
    """Regression guard: gpu/timeout/volumes must reach modal.Sandbox.create
    unchanged — a common typo-class bug (e.g. swapping args) would silently
    run every sandbox on CPU or with the wrong timeout."""
    from services.sandbox import run_code

    fake_sb = _FakeSandbox(stdout_chunks=[], stderr_chunks=[], returncode=0)
    create_aio = _patch_create(monkeypatch, fake_sb)

    await run_code(
        code="print(1)",
        session_id="sess-7",
        stage="train",
        gpu="A10G",
        timeout=42,
    )

    kwargs = create_aio.call_args.kwargs
    assert kwargs["gpu"] == "A10G"
    assert kwargs["timeout"] == 42
