"""A5 — programming errors in a provider module must propagate.

Pre-fix: `_bootstrap` used `except Exception:` and logged at debug.
A typo / AttributeError / RuntimeError inside `claude_provider.py`
silently dropped the provider; users later got
`Unknown LLM provider 'claude'` with no hint why.

Post-fix: only `ImportError` is suppressed; everything else raises.

This script:
  1. Confirms a clean bootstrap registers the expected providers.
  2. Installs a fake `services.llm.claude_provider` whose attribute
     lookup raises RuntimeError. The bootstrap now propagates it.

Run:
    cd backend && .venv/bin/python scripts/bug_validation/exp_factory_narrow_except.py
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def main() -> int:
    from services.llm import factory

    # 1. Clean bootstrap — Claude registers as both `claude` and `anthropic`.
    factory._REGISTRY.clear()
    factory._INSTANCES.clear()
    factory._bootstrap()
    ids = set(factory.list_providers())
    print(f"  clean bootstrap providers: {sorted(ids)}")
    assert "openai" in ids and "litellm" in ids

    # 2. Inject a broken module that raises RuntimeError on attribute lookup.
    factory._REGISTRY.clear()
    factory._INSTANCES.clear()

    bad_mod = types.ModuleType("services.llm.claude_provider")

    def _explode(name):
        raise RuntimeError(f"simulated provider bug while resolving {name!r}")

    bad_mod.__getattr__ = _explode  # type: ignore[attr-defined]
    saved = sys.modules.get("services.llm.claude_provider")
    sys.modules["services.llm.claude_provider"] = bad_mod
    try:
        try:
            factory._bootstrap()
            print("FAIL — bootstrap silently swallowed RuntimeError")
            return 1
        except RuntimeError as e:
            print(f"  raised: {e}")
    finally:
        if saved is not None:
            sys.modules["services.llm.claude_provider"] = saved
        else:
            sys.modules.pop("services.llm.claude_provider", None)

    print("PASS — non-ImportError errors propagate; only ImportError suppressed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
