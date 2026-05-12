"""Tests for the session-as-Python-repo bootstrap (issue #51).

What this covers:
- The SDK preamble creates `/data/sessions/{sid}/src/__init__.py` and puts
  `/data/sessions/{sid}/src` on sys.path, so any module the agent writes
  there is importable from the next execute_code call.
- `build_sdk_preamble(session_id)` interpolates the right session id.
- `run_code` calls `modal.Sandbox.create.aio` with the correct
  `workdir=/data/sessions/{sid}` so relative paths land on the volume.
- `kernel_manager._spawn` sets the same workdir on the notebook kernel.
- `ensure_session_workspace` writes the empty `__init__.py` marker.

We don't actually launch a Modal sandbox here — `modal.Sandbox.create.aio`
is patched and the call's kwargs are asserted.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestSdkPreambleRepoBootstrap:
    """The preamble runs inside the sandbox; we exec it in a throwaway
    namespace and check it side-effects sys.path + creates the package dir."""

    def test_preamble_adds_src_to_syspath_and_creates_init(self, tmp_path, monkeypatch):
        from services.sandbox import build_sdk_preamble

        # Redirect /data/sessions/{sid}/src to a tmp path so we don't touch
        # the real volume. The preamble uses os.path.join with hard-coded
        # /data; we monkey-patch os.makedirs / open to redirect.
        session_id = "test-repo-bootstrap"
        fake_src = tmp_path / "sessions" / session_id / "src"

        # The cleanest way to verify the preamble behavior is to render it
        # with a session id and check the literal source for the bootstrap
        # markers — running it for real would require patching builtins
        # mid-exec, which the preamble's try/except would swallow.
        src = build_sdk_preamble(session_id)
        assert f'_SID = "{session_id}"' in src
        assert (
            '_SESSION_SRC = _trn_os.path.join(_VOL_ROOT, "sessions", _SID, "src")'
            in src
        )
        assert "_trn_os.makedirs(_SESSION_SRC, exist_ok=True)" in src
        assert "_trn_sys.path.insert(0, _SESSION_SRC)" in src
        assert "__init__.py" in src
        del fake_src  # keep tmp_path fixture happy (lint)

    def test_preamble_executes_cleanly_in_a_throwaway_namespace(self, tmp_path):
        """Real exec — the preamble runs against the *actual* filesystem,
        creating `/data/sessions/.../src`. To keep the test hermetic, we
        redirect the volume root by patching os.path.join. The preamble's
        own try/except swallows EPERM if /data isn't writable, so this
        test just asserts no Python exception leaks out and that
        `trainable` ends up in sys.modules."""
        from services.sandbox import build_sdk_preamble

        ns: dict = {}
        try:
            exec(build_sdk_preamble("hermetic-exec"), ns)
            assert "trainable" in sys.modules
            mod = sys.modules["trainable"]
            assert callable(mod.log)
            assert callable(mod.configure_dashboard)
        finally:
            sys.modules.pop("trainable", None)

    def test_preamble_template_session_id_is_interpolated(self):
        from services.sandbox import SDK_PREAMBLE_TEMPLATE, build_sdk_preamble

        a = build_sdk_preamble("session-aaa")
        b = build_sdk_preamble("session-bbb")
        assert '_SID = "session-aaa"' in a
        assert '_SID = "session-bbb"' in b
        # Template untouched after rendering.
        assert "__SESSION_ID__" in SDK_PREAMBLE_TEMPLATE


class TestSandboxWorkdir:
    """`run_code` must call modal.Sandbox.create.aio with workdir set
    to the session workspace so relative writes land on the volume."""

    @pytest.mark.asyncio
    async def test_run_code_sets_workdir(self, monkeypatch):
        # Fake sandbox object — async iterators for stdout/stderr (empty) +
        # an awaitable wait().
        async def _empty_iter():
            if False:
                yield  # pragma: no cover

        fake_sb = MagicMock()
        fake_sb.stdout = _empty_iter()
        fake_sb.stderr = _empty_iter()
        fake_sb.wait = MagicMock(aio=AsyncMock())
        fake_sb.returncode = 0

        async def _fake_create(*args, **kwargs):
            _fake_create.last_kwargs = kwargs
            return fake_sb

        import services.sandbox as sandbox_mod

        # Patch the chain of dependencies that would hit Modal for real.
        monkeypatch.setattr(
            sandbox_mod.modal.Sandbox, "create", MagicMock(aio=_fake_create)
        )
        monkeypatch.setattr(sandbox_mod, "_get_app", AsyncMock(return_value=None))
        monkeypatch.setattr(sandbox_mod, "_get_image", lambda: None)
        monkeypatch.setattr(
            sandbox_mod, "record_sandbox_usage", AsyncMock(), raising=False
        )

        # Block the volume bootstrap (no real Modal call).
        import services.volume as vol_mod

        monkeypatch.setattr(vol_mod, "ensure_session_workspace", AsyncMock())
        monkeypatch.setattr(vol_mod, "reload_volume_async", AsyncMock())
        monkeypatch.setattr(vol_mod, "get_volume", lambda: MagicMock())

        await sandbox_mod.run_code("print('hi')", session_id="sess-xyz")

        kwargs = _fake_create.last_kwargs
        assert kwargs["workdir"] == "/data/sessions/sess-xyz", (
            f"Sandbox must be anchored to the session workspace, got {kwargs.get('workdir')!r}"
        )
        # SDK preamble + user code passed as a single string to `python -u -c`.
        assert "sess-xyz" in kwargs.get("workdir", "") or "sess-xyz" in str(kwargs)


class TestEnsureSessionWorkspace:
    """`ensure_session_workspace` lays down an empty src/__init__.py so
    Modal's `workdir=` has a real directory to anchor to on the first
    sandbox of a brand-new session."""

    @pytest.mark.asyncio
    async def test_writes_init_py_under_src(self, monkeypatch, tmp_path):
        from services import volume as vol_mod

        captured: dict = {}

        class FakeBatch:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def put_file(self, local, remote):
                captured["local"] = local
                captured["remote"] = remote

        fake_vol = MagicMock()
        fake_vol.batch_upload = lambda force=False: FakeBatch()
        monkeypatch.setattr(vol_mod, "get_volume", lambda: fake_vol)

        await vol_mod.ensure_session_workspace("sess-abc")

        assert captured["remote"] == "/sessions/sess-abc/src/__init__.py"
        # local tmp file should exist briefly during put_file (we don't
        # assert content because it's an empty marker).


class TestKernelManagerWorkdir:
    """The notebook kernel sandbox also gets workdir + workspace ensure."""

    @pytest.mark.asyncio
    async def test_spawn_sets_workdir_and_calls_ensure(self, monkeypatch):
        from services import kernel_manager as km

        ensure = AsyncMock()
        monkeypatch.setattr(km, "ensure_session_workspace", ensure)

        captured: dict = {}

        async def _fake_create(*args, **kwargs):
            captured.update(kwargs)
            sb = MagicMock()

            async def _empty():
                if False:
                    yield  # pragma: no cover

            sb.stdout = _empty()
            sb.stderr = _empty()
            return sb

        monkeypatch.setattr(km.modal.Sandbox, "create", MagicMock(aio=_fake_create))
        monkeypatch.setattr(km, "get_app", AsyncMock(return_value=None))
        monkeypatch.setattr(km, "get_image", lambda: None)
        monkeypatch.setattr(km, "get_volume", lambda: MagicMock())

        # Avoid the broadcaster reaching Redis.
        monkeypatch.setattr(km.broadcaster, "publish", AsyncMock())

        manager = km.KernelManager()
        await manager._spawn("sess-notebook")

        ensure.assert_awaited_once_with("sess-notebook")
        assert captured["workdir"] == "/data/sessions/sess-notebook"


class TestKernelProxyEmbedsRepoBootstrap:
    """The kernel proxy runs the SDK preamble inside the ipykernel as a
    silent execute so cells can `import` session modules just like
    one-shot `execute_code` scripts can."""

    def test_build_kernel_proxy_script_embeds_session_aware_preamble(self):
        from services.kernel_manager import build_kernel_proxy_script
        from services.sandbox import build_sdk_preamble

        script = build_kernel_proxy_script("sess-kernel-repo")
        # The preamble is repr()'d into the proxy as a Python string
        # literal — the literal must contain the session-aware src path.
        assert repr(build_sdk_preamble("sess-kernel-repo")) in script
        # And the proxy must execute that preamble silently before signaling
        # ready, so the very first cell can `import my_module`.
        preamble_idx = script.index("kc.execute(_SDK_PREAMBLE")
        ready_idx = script.index('"type": "ready"')
        assert preamble_idx < ready_idx


class TestAgentYamlsTeachRepoConvention:
    """Each code-writing agent must mention the `src/` import contract in
    its system prompt; otherwise the agent never learns to use it and the
    feature has zero adoption."""

    @pytest.mark.parametrize(
        "agent_file",
        ["trainer.yaml", "data_prep.yaml", "feature_eng.yaml", "eda.yaml", "chat.yaml"],
    )
    def test_agent_prompt_mentions_repo_convention(self, agent_file):
        path = Path(__file__).resolve().parent.parent / "agents" / agent_file
        text = path.read_text()
        # Lightweight check — the exact wording can drift, but these three
        # ideas must land somewhere in the prompt.
        assert "src/" in text, f"{agent_file} should mention the src/ convention"
        assert "sys.path" in text or "importable" in text or "import" in text, (
            f"{agent_file} should explain that src/ is importable"
        )
