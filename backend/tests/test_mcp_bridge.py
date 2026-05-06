"""Unit tests for skills/mcp_bridge.py — the Claude-side MCP adapter.

The bridge turns (agent_type) + (per-call context) into a {slug: entry} dict
where each entry has description/input_schema/handler. We verify slug
gating (delegate-task depth check), schema injection, and unknown-skill
warnings.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def sandbox_skills(tmp_path: Path, monkeypatch):
    """Tmpdir that the registry treats as the skills root."""
    from services.skills import registry

    monkeypatch.setattr(registry, "_SKILLS_ROOT", tmp_path)
    registry.discover_skills.cache_clear()
    yield tmp_path
    registry.discover_skills.cache_clear()


def _make_skill(
    root: Path, slug: str, *, schema: dict | None = None, has_handler: bool = True
):
    d = root / slug
    d.mkdir(exist_ok=True)
    fm = {"name": slug, "description": f"{slug} desc", "version": "0.1"}
    (d / "SKILL.md").write_text("---\n" + yaml.safe_dump(fm).strip() + "\n---\n\n")
    if has_handler:
        (d / "handler.py").write_text(
            "def create_handler(**ctx):\n"
            "    async def handler(args):\n"
            f"        return {{'content': [{{'type':'text','text':'{slug}-ran'}}]}}\n"
            "    return handler\n"
        )
    if schema is not None:
        (d / "schema.yaml").write_text(yaml.safe_dump(schema))


def _patch_agent(
    monkeypatch, *, skills: list[str], subagents: list[str] = (), max_depth: int = 0
):
    """Stub services.agent.agents so the bridge sees the skill list we want."""
    import services.agent.agents as agents_mod

    monkeypatch.setattr(agents_mod, "get_agent_skills", lambda _t: skills)
    monkeypatch.setattr(
        agents_mod,
        "can_delegate",
        lambda agent_type, depth: bool(subagents) and depth < max_depth,
    )
    monkeypatch.setattr(
        agents_mod,
        "get_skill_for_agent",
        lambda agent_type, slug: {
            "name": slug,
            "description": f"{slug} desc",
            "input_schema": {"type": "object", "properties": {}},
        },
    )
    monkeypatch.setattr(
        agents_mod, "render_skill_description", lambda **kw: f"{kw['skill_slug']} desc"
    )
    monkeypatch.setattr(
        agents_mod,
        "get_skill_input_schema",
        lambda slug, agent_type: {"type": "object", "properties": {}},
    )
    monkeypatch.setattr(
        agents_mod,
        "list_delegatable_agents",
        lambda _t: [{"type": s, "description": f"{s} agent"} for s in subagents],
    )


class TestBuildSkillEntries:
    def test_returns_entries_for_each_slug(self, sandbox_skills, monkeypatch):
        from services.skills.mcp_bridge import build_skill_entries

        _make_skill(sandbox_skills, "execute-code")
        _make_skill(sandbox_skills, "read-notebook")
        _patch_agent(monkeypatch, skills=["execute-code", "read-notebook"])

        async def _publish(*a, **k):
            pass

        entries = build_skill_entries(
            agent_type="eda",
            session_id="s",
            experiment_id="e",
            stage="eda",
            depth=0,
            publish_fn=_publish,
        )
        assert set(entries.keys()) == {"execute-code", "read-notebook"}
        for entry in entries.values():
            assert "handler" in entry
            assert "description" in entry
            assert "input_schema" in entry

    def test_unknown_slug_warning_skipped(self, sandbox_skills, monkeypatch, caplog):
        from services.skills.mcp_bridge import build_skill_entries

        _patch_agent(monkeypatch, skills=["does-not-exist"])

        async def _publish(*a, **k):
            pass

        with caplog.at_level("WARNING"):
            entries = build_skill_entries(
                agent_type="eda",
                session_id="s",
                experiment_id="e",
                stage="eda",
                depth=0,
                publish_fn=_publish,
            )
        assert entries == {}
        assert any("does-not-exist" in r.message for r in caplog.records)

    def test_delegate_task_gated_by_depth(self, sandbox_skills, monkeypatch):
        from services.skills.mcp_bridge import build_skill_entries

        _make_skill(
            sandbox_skills,
            "delegate-task",
            schema={"type": "object", "properties": {"agent_type": {}}},
        )
        _patch_agent(
            monkeypatch, skills=["delegate-task"], subagents=["worker"], max_depth=1
        )

        async def _publish(*a, **k):
            pass

        # depth=0 with max_depth=1 -> can_delegate True
        entries = build_skill_entries(
            agent_type="eda",
            session_id="s",
            experiment_id="e",
            stage="eda",
            depth=0,
            publish_fn=_publish,
        )
        assert "delegate-task" in entries

        # depth=1 == max_depth -> can_delegate False -> dropped
        entries = build_skill_entries(
            agent_type="eda",
            session_id="s",
            experiment_id="e",
            stage="eda",
            depth=1,
            publish_fn=_publish,
        )
        assert "delegate-task" not in entries

    def test_delegate_task_injects_agent_enum(self, sandbox_skills, monkeypatch):
        from services.skills.mcp_bridge import build_skill_entries
        import services.agent.agents as agents_mod

        _make_skill(sandbox_skills, "delegate-task")
        # delegate-task needs an input_schema with an agent_type enum slot to populate.
        monkeypatch.setattr(
            agents_mod, "get_agent_skills", lambda _t: ["delegate-task"]
        )
        monkeypatch.setattr(agents_mod, "can_delegate", lambda *_a, **_k: True)
        monkeypatch.setattr(
            agents_mod,
            "get_skill_for_agent",
            lambda agent_type, slug: {
                "name": slug,
                "description": "delegate-task desc",
                "input_schema": {
                    "type": "object",
                    "properties": {"agent_type": {"type": "string"}},
                },
            },
        )
        monkeypatch.setattr(
            agents_mod, "render_skill_description", lambda **kw: "delegate-task desc"
        )
        monkeypatch.setattr(
            agents_mod,
            "get_skill_input_schema",
            lambda slug, agent_type: {
                "type": "object",
                "properties": {"agent_type": {"type": "string"}},
            },
        )
        monkeypatch.setattr(
            agents_mod,
            "list_delegatable_agents",
            lambda _t: [
                {"type": "data_prep", "description": "prep agent"},
                {"type": "trainer", "description": "training agent"},
            ],
        )

        async def _publish(*a, **k):
            pass

        entries = build_skill_entries(
            agent_type="eda",
            session_id="s",
            experiment_id="e",
            stage="eda",
            depth=0,
            publish_fn=_publish,
        )
        schema = entries["delegate-task"]["input_schema"]
        assert schema["properties"]["agent_type"]["enum"] == ["data_prep", "trainer"]
        # Description suffix should advertise both choices.
        assert "data_prep" in entries["delegate-task"]["description"]
        assert "trainer" in entries["delegate-task"]["description"]


class TestBuildMcpServer:
    def test_returns_mcp_descriptor(self, sandbox_skills, monkeypatch):
        from services.skills import mcp_bridge

        _make_skill(sandbox_skills, "execute-code")
        _patch_agent(monkeypatch, skills=["execute-code"])

        # Stub the actual MCP server creator so we don't depend on the mcp package.
        captured = {}

        def fake_create(entries):
            captured["entries"] = entries
            return {"type": "sdk", "name": "trainable", "instance": object()}

        monkeypatch.setattr(mcp_bridge, "_create_mcp_server", lambda: fake_create)

        async def _publish(*a, **k):
            pass

        server = mcp_bridge.build_mcp_server(
            agent_type="eda",
            session_id="s",
            experiment_id="e",
            stage="eda",
            publish_fn=_publish,
        )
        assert server["name"] == "trainable"
        assert "execute-code" in captured["entries"]
