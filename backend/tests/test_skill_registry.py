"""Unit tests for the unified skill registry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def tmp_skills(tmp_path: Path, monkeypatch):
    """Point the registry at a tmp dir of fake skills and return the dir."""
    from services.skills import registry

    monkeypatch.setattr(registry, "_SKILLS_ROOT", tmp_path)
    registry.discover_skills.cache_clear()
    yield tmp_path
    registry.discover_skills.cache_clear()


def _write_skill(
    root: Path,
    slug: str,
    *,
    description: str = "do a thing",
    when_to_use: str = "always",
    handler: str | None = None,
    schema: dict | None = None,
    body: str = "",
):
    skill_dir = root / slug
    skill_dir.mkdir()
    fm = {
        "name": slug,
        "description": description,
        "when_to_use": when_to_use,
        "version": "0.1",
    }
    fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
    (skill_dir / "SKILL.md").write_text(f"---\n{fm_text}\n---\n\n{body}")
    if handler is not None:
        (skill_dir / "handler.py").write_text(handler)
    if schema is not None:
        (skill_dir / "schema.yaml").write_text(yaml.safe_dump(schema))


HANDLER_SRC = (
    "def create_handler(**ctx):\n"
    "    async def handler(args):\n"
    "        return {'content': [{'type': 'text', 'text': 'ok ' + str(args)}]}\n"
    "    return handler\n"
)


class TestDiscover:
    def test_empty(self, tmp_skills):
        from services.skills.registry import discover_skills

        assert discover_skills() == {}

    def test_skill_without_handler_is_knowledge(self, tmp_skills):
        from services.skills.registry import (
            discover_skills,
            get_capability_skills,
            get_knowledge_skills,
        )

        _write_skill(tmp_skills, "doc-only", body="# Just docs\n")
        skills = discover_skills()
        assert "doc-only" in skills
        s = skills["doc-only"]
        assert not s.has_handler
        assert s.kind == "knowledge"
        assert s in get_knowledge_skills()
        assert s not in get_capability_skills()

    def test_skill_with_handler_is_capability(self, tmp_skills):
        from services.skills.registry import discover_skills, get_capability_skills

        _write_skill(
            tmp_skills,
            "do-thing",
            handler=HANDLER_SRC,
            schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        s = discover_skills()["do-thing"]
        assert s.has_handler
        assert s.kind == "capability"
        assert s in get_capability_skills()
        assert s.schema["properties"]["x"]["type"] == "string"

    def test_hybrid_skill(self, tmp_skills):
        from services.skills.registry import discover_skills

        _write_skill(
            tmp_skills,
            "hybrid",
            handler=HANDLER_SRC,
            schema={"type": "object"},
            body="# methodology\n\nDo this then that.\n",
        )
        assert discover_skills()["hybrid"].kind == "hybrid"

    def test_directory_without_skill_md_skipped(self, tmp_skills):
        from services.skills.registry import discover_skills

        (tmp_skills / "junk").mkdir()
        assert discover_skills() == {}

    def test_unknown_slug_raises(self, tmp_skills):
        from services.skills.registry import get_skill

        with pytest.raises(KeyError):
            get_skill("nope")


class TestNormalizedSpecs:
    def test_returns_capability_specs(self, tmp_skills):
        from services.skills.registry import get_normalized_specs

        _write_skill(
            tmp_skills,
            "exec",
            handler=HANDLER_SRC,
            schema={"type": "object", "properties": {"code": {"type": "string"}}},
            description="Run code",
        )
        specs = get_normalized_specs(["exec"])
        assert len(specs) == 1
        spec = specs[0]
        assert spec["name"] == "exec"
        assert spec["description"] == "Run code"
        assert "code" in spec["input_schema"]["properties"]

    def test_skips_knowledge_skills(self, tmp_skills):
        from services.skills.registry import get_normalized_specs

        _write_skill(tmp_skills, "doc-only", body="# docs")
        assert get_normalized_specs(["doc-only"]) == []

    def test_skips_unknown_slugs(self, tmp_skills):
        from services.skills.registry import get_normalized_specs

        assert get_normalized_specs(["does-not-exist"]) == []

    def test_default_schema_when_missing(self, tmp_skills):
        from services.skills.registry import get_normalized_specs

        # capability skill without schema.yaml -> spec gets a permissive empty schema
        _write_skill(tmp_skills, "no-schema", handler=HANDLER_SRC)
        specs = get_normalized_specs(["no-schema"])
        assert specs[0]["input_schema"] == {"type": "object", "properties": {}}


class TestLoadHandler:
    @pytest.mark.asyncio
    async def test_loads_and_invokes(self, tmp_skills):
        from services.skills.registry import load_handler

        _write_skill(tmp_skills, "echo", handler=HANDLER_SRC, schema={"type": "object"})
        factory = load_handler("echo")
        handler = factory(session_id="s")
        result = await handler({"foo": "bar"})
        assert "ok" in result["content"][0]["text"]

    def test_raises_on_knowledge_skill(self, tmp_skills):
        from services.skills.registry import load_handler

        _write_skill(tmp_skills, "doc-only", body="# docs")
        with pytest.raises(ValueError, match="knowledge-only"):
            load_handler("doc-only")


class TestLoadSkill:
    def test_returns_body_and_files(self, tmp_skills):
        from services.skills.registry import load_skill

        _write_skill(tmp_skills, "method", body="# Methodology\n\nstep 1\n")
        # Add a supporting file
        (tmp_skills / "method" / "scripts").mkdir()
        (tmp_skills / "method" / "scripts" / "helper.py").write_text("print(1)\n")
        out = load_skill("method")
        assert "step 1" in out["body"]
        assert any(f["path"] == "scripts/helper.py" for f in out["files"])
        assert out["sandbox_root"] == "/skills/method"
