"""Smoke tests for the Tier 3 features (registry, snapshots, dataset versions).

These exercise the service layer in isolation — actual Modal/volume work
is mocked so they run in unit-test time.
"""

from __future__ import annotations

import pytest

from services.dataset_versions import hash_bytes


def test_hash_bytes_deterministic():
    a = hash_bytes(b"hello world")
    b = hash_bytes(b"hello world")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_hash_bytes_distinguishes_content():
    assert hash_bytes(b"abc") != hash_bytes(b"abd")


def test_compare_input_validation():
    """Compare endpoint should reject too-many-sessions requests."""
    from routers.compare import compare

    # Too many sessions
    import asyncio

    async def call():
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await compare(",".join([f"s{i}" for i in range(10)]))
        assert exc.value.status_code == 400

    asyncio.run(call())


def test_compare_empty_input_rejected():
    from routers.compare import compare
    import asyncio

    async def call():
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await compare("")
        assert exc.value.status_code == 400

    asyncio.run(call())


def test_deploy_app_naming_idempotent():
    """Deployment app/function names should be deterministic so refreshes hit
    the same Modal target."""
    from services.deploy import _modal_app_name, _modal_function_name

    project_id = "abc-123"
    a = _modal_app_name(project_id)
    b = _modal_app_name(project_id)
    assert a == b
    assert "serving" in a

    fn1 = _modal_function_name("Customer Churn", 3)
    assert fn1 == "customer-churn-v3"


def test_deploy_function_name_handles_weird_chars():
    from services.deploy import _modal_function_name

    fn = _modal_function_name("Crazy~~Name!! With $$ chars", 1)
    # Only [a-z0-9-] allowed.
    assert all(c.isalnum() or c == "-" for c in fn)
    assert fn.endswith("-v1")
