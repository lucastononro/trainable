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
    enables: list[str] | None = None,
):
    skill_dir = root / slug
    skill_dir.mkdir()
    fm: dict = {
        "name": slug,
        "description": description,
        "when_to_use": when_to_use,
        "version": "0.1",
    }
    if enables is not None:
        fm["enables"] = enables
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


class TestEnablesFrontmatter:
    def test_default_is_empty_list(self, tmp_skills):
        from services.skills.registry import discover_skills

        _write_skill(tmp_skills, "plain", body="# docs")
        assert discover_skills()["plain"].enables == []

    def test_parses_list(self, tmp_skills):
        from services.skills.registry import discover_skills, load_skill

        _write_skill(
            tmp_skills,
            "weather",
            body="# weather playbook",
            enables=["get-wind-speed", "fetch-humidity"],
        )
        skill = discover_skills()["weather"]
        assert skill.enables == ["get-wind-speed", "fetch-humidity"]
        # load_skill exposes enables on the dict the use-skill handler consumes.
        assert load_skill("weather")["enables"] == [
            "get-wind-speed",
            "fetch-humidity",
        ]

    def test_string_coerced_to_list(self, tmp_skills):
        from services.skills.registry import discover_skills

        _write_skill(tmp_skills, "single", body="# docs", enables="just-one")
        assert discover_skills()["single"].enables == ["just-one"]

    def test_blank_entries_dropped(self, tmp_skills):
        from services.skills.registry import discover_skills

        _write_skill(tmp_skills, "messy", body="# docs", enables=["a", "", "  ", "b"])
        assert discover_skills()["messy"].enables == ["a", "b"]


class TestActiveToolsState:
    def setup_method(self):
        from services.skills import state

        state._active_tools.clear()

    def test_activate_returns_only_new_slugs(self):
        from services.skills.state import activate_tools, get_active_tools

        added = activate_tools("sess", "agent", ["a", "b"])
        assert added == ["a", "b"]
        # Re-adding overlaps reports only the new one.
        added2 = activate_tools("sess", "agent", ["a", "c"])
        assert added2 == ["c"]
        assert get_active_tools("sess", "agent") == {"a", "b", "c"}

    def test_scope_per_agent(self):
        from services.skills.state import activate_tools, get_active_tools

        activate_tools("sess", "root", ["a"])
        activate_tools("sess", "child-1", ["b"])
        assert get_active_tools("sess", "root") == {"a"}
        assert get_active_tools("sess", "child-1") == {"b"}
        # Other (session, agent) pairs see an empty set, never each other's.
        assert get_active_tools("sess", "child-2") == set()

    def test_cleanup_clears_all_agents_in_session(self):
        from services.skills.state import (
            activate_tools,
            cleanup_session,
            get_active_tools,
        )

        activate_tools("sess-A", "root", ["a"])
        activate_tools("sess-A", "child", ["b"])
        activate_tools("sess-B", "root", ["c"])

        cleanup_session("sess-A")

        assert get_active_tools("sess-A", "root") == set()
        assert get_active_tools("sess-A", "child") == set()
        assert get_active_tools("sess-B", "root") == {"c"}

    def test_empty_input_no_op(self):
        from services.skills.state import activate_tools, get_active_tools

        assert activate_tools("sess", "agent", []) == []
        assert get_active_tools("sess", "agent") == set()


class TestScriptFilenameSeeding:
    """Regression for A12: in-process `_code_counter` reset on restart
    used to collide with `step_NN_*.py` files already on the volume.
    After the fix, the counter seeds from the highest on-volume index
    on first call per session.
    """

    def setup_method(self):
        from services.skills import state

        state._code_counter.clear()

    def teardown_method(self):
        from services.skills import state

        state._code_counter.clear()

    def test_seeds_counter_past_existing_step_files(self, monkeypatch):
        from services.skills import state

        class _FakeEntry:
            def __init__(self, path: str):
                self.path = path

        fake_listing = [
            _FakeEntry("/sessions/abc/scripts/step_01_load.py"),
            _FakeEntry("/sessions/abc/scripts/step_05_train.py"),
            _FakeEntry("/sessions/abc/scripts/step_07_eval.py"),
            _FakeEntry("/sessions/abc/scripts/notes.md"),
        ]

        class _FakeVolume:
            def listdir(self, path):
                assert path == "/sessions/abc/scripts"
                return iter(fake_listing)

        monkeypatch.setattr(
            "services.volume.get_volume", lambda: _FakeVolume(), raising=True
        )

        # No prior in-process counter — simulates fresh backend after restart.
        name = state._script_filename("print('hello')", "abc")
        # Highest existing was 07 → next call must produce 08.
        assert name.startswith("step_08_"), name

    def test_falls_back_to_one_when_volume_empty(self, monkeypatch):
        from services.skills import state

        class _FakeVolume:
            def listdir(self, path):
                return iter([])

        monkeypatch.setattr(
            "services.volume.get_volume", lambda: _FakeVolume(), raising=True
        )

        name = state._script_filename("# first run", "fresh-session")
        assert name.startswith("step_01_"), name

    def test_in_process_counter_takes_precedence_over_volume(self, monkeypatch):
        """After the first call seeds from the volume, subsequent calls
        increment the in-memory counter without re-probing — otherwise
        we'd pay a Modal round-trip per step."""
        from services.skills import state

        probes = {"n": 0}

        class _FakeVolume:
            def listdir(self, path):
                probes["n"] += 1
                return iter([])

        monkeypatch.setattr(
            "services.volume.get_volume", lambda: _FakeVolume(), raising=True
        )

        state._script_filename("# a", "sid")
        state._script_filename("# b", "sid")
        state._script_filename("# c", "sid")
        # Volume listdir only on the first call; the next two read from
        # the in-process counter.
        assert probes["n"] == 1


class TestUseSkillActivation:
    """Drive the use-skill handler against a tmp skills tree to verify it
    activates declared `enables` and surfaces them in the response text."""

    def setup_method(self):
        from services.skills import state

        state._active_tools.clear()

    @pytest.mark.asyncio
    async def test_loading_activates_enables(self, tmp_skills, monkeypatch):
        # The use-skill handler imports `services.skills` at call time;
        # registry tests use a tmp _SKILLS_ROOT, so we just need the
        # registry to find our fake skills there.
        _write_skill(
            tmp_skills,
            "wind-speed",
            handler=HANDLER_SRC,
            schema={"type": "object"},
            description="get wind speed",
        )
        _write_skill(
            tmp_skills,
            "weather",
            body="# weather playbook",
            enables=["wind-speed"],
        )

        # Load the use-skill handler module from disk so the import path
        # used by registry.load_handler matches production.
        import importlib.util

        handler_path = (
            Path(__file__).parent.parent / "skills" / "use-skill" / "handler.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_test_use_skill_handler", handler_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        from services.skills.state import get_active_tools

        handler = module.create_handler(session_id="sess", parent_agent_id="agent-1")
        result = await handler({"slug": "weather"})
        assert result.get("is_error") is not True
        text = result["content"][0]["text"]
        # The activated tool is announced to the model.
        assert "wind-speed" in text
        assert "Tools enabled by this skill" in text
        # And the active set is updated for the right (session, agent) key.
        assert get_active_tools("sess", "agent-1") == {"wind-speed"}

    @pytest.mark.asyncio
    async def test_unknown_enables_are_dropped(self, tmp_skills):
        _write_skill(
            tmp_skills,
            "weather",
            body="# weather playbook",
            enables=["does-not-exist"],
        )

        import importlib.util

        handler_path = (
            Path(__file__).parent.parent / "skills" / "use-skill" / "handler.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_test_use_skill_handler_2", handler_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        from services.skills.state import get_active_tools

        handler = module.create_handler(session_id="sess", parent_agent_id="agent-1")
        result = await handler({"slug": "weather"})
        text = result["content"][0]["text"]
        # No "Tools enabled" section should appear when nothing valid resolves.
        assert "Tools enabled by this skill" not in text
        assert get_active_tools("sess", "agent-1") == set()
