"""Tests for services/kernel_manager.py — proxy-script assembly."""

import ast

from services.kernel_manager import KERNEL_PROXY_SCRIPT
from services.sandbox import SDK_PREAMBLE


class TestProxyScriptAssembly:
    """The proxy script is stamped out with the SDK preamble embedded as a
    Python string literal. If either the template or SDK_PREAMBLE drift into
    something that doesn't parse, cells silently fail — catch that here."""

    def test_proxy_script_is_valid_python(self):
        ast.parse(KERNEL_PROXY_SCRIPT)

    def test_proxy_script_embeds_preamble_verbatim(self):
        assert "_SDK_PREAMBLE = " in KERNEL_PROXY_SCRIPT
        assert repr(SDK_PREAMBLE) in KERNEL_PROXY_SCRIPT

    def test_proxy_script_runs_preamble_silently_before_ready(self):
        # Preamble must run before cells (so `trainable` is importable from
        # the first cell) and must be silent + not bump execution counters.
        assert (
            "kc.execute(_SDK_PREAMBLE, silent=True, store_history=False)"
            in KERNEL_PROXY_SCRIPT
        )
        preamble_idx = KERNEL_PROXY_SCRIPT.index("kc.execute(_SDK_PREAMBLE")
        ready_idx = KERNEL_PROXY_SCRIPT.index('"type": "ready"')
        assert preamble_idx < ready_idx, "preamble must execute before ready is emitted"

    def test_sdk_preamble_registers_trainable_module(self):
        # Sanity-check the preamble itself — run it in a throwaway namespace
        # and confirm `trainable` ends up in sys.modules with log + configure_dashboard.
        import sys

        ns = {}
        try:
            exec(SDK_PREAMBLE, ns)
            assert "trainable" in sys.modules
            mod = sys.modules["trainable"]
            assert callable(mod.log)
            assert callable(mod.configure_dashboard)
        finally:
            sys.modules.pop("trainable", None)
