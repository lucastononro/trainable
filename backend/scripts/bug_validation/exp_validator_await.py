"""B2 — `_read_volume_file_safe(...)` must be awaited inside
`validate_train_output`.

Pre-fix at validator.py:396, the call was synchronous-looking; the
returned coroutine was truthy, the fallback scan was skipped, and
`len(coroutine)` later raised `TypeError`, aborting validation halfway.

This script simulates the Artifact-row branch with a MockVolume and
seeded DB rows, runs the validator, and asserts the function returns a
well-formed result with `report.md exists` in passed[].

Run:
    cd backend && .venv/bin/python scripts/bug_validation/exp_validator_await.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import ExitStack

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


async def main() -> int:
    from db import Base, async_session, engine
    from models import Artifact, Project, Session
    from tests.conftest import MockVolume, mock_volume_patches

    train_meta = json.dumps(
        {
            "best_model": "RFClassifier",
            "test_metrics": {"accuracy": 0.83, "f1_weighted": 0.81},
        }
    ).encode()
    files = {
        "/sessions/exp-await/models/model.pkl": b"fake-bytes",
        "/sessions/exp-await/reports/train_report.md": b"# Train Report\nRF.",
        "/sessions/exp-await/data/metadata.json": train_meta,
    }
    vol = MockVolume(files)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with async_session() as db:
            db.add(Project(id="proj-await", name="P"))
            db.add(Session(id="exp-await", project_id="proj-await"))
            await db.commit()
            db.add(
                Artifact(
                    session_id="exp-await",
                    stage="train",
                    artifact_type="report",
                    name="train_report.md",
                    path="/sessions/exp-await/reports/train_report.md",
                    created_at="2026-05-12T00:00:00",
                )
            )
            await db.commit()

        with ExitStack() as stack:
            for p in mock_volume_patches(vol, "services.validator"):
                stack.enter_context(p)
            from services.validator import validate_train_output

            # Pre-fix this raised TypeError("object of type 'coroutine' has no len()").
            result = await validate_train_output("exp-await", "exp-id")

        passed = " ".join(result.get("passed", []))
        errors = " ".join(result.get("errors", []))
        warnings = " ".join(result.get("warnings", []))

        print(f"  stage    : {result['stage']}")
        print(f"  passed   : {passed[:120]}...")
        print(f"  warnings : {warnings or '(none)'}")
        print(f"  errors   : {errors or '(none)'}")

        assert result["stage"] == "train"
        assert "report.md exists" in passed, "Artifact-resolved report not reported"
        # The pre-fix TypeError aborted before metadata could be read.
        assert "metadata.json has model and test metrics" in passed
        print("PASS — Artifact-based report discovery reaches downstream checks")
        return 0
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
