"""Skill discovery and loading.

Skills live at backend/skills/<slug>/. Each skill has at minimum a SKILL.md
file with YAML frontmatter. Capability skills additionally bundle a handler.py
and a schema.yaml (JSON-schema for the handler's input args).
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import yaml

logger = logging.getLogger(__name__)

_SKILLS_ROOT = Path(__file__).parent.parent.parent / "skills"

# Maximum bytes of SKILL.md body we will embed in a single use-skill response.
_MAX_SKILL_BYTES = 64_000


@dataclass
class Skill:
    slug: str
    name: str
    description: str
    when_to_use: str
    version: str
    body: str
    has_handler: bool
    schema: dict
    files: list[dict] = field(default_factory=list)
    # Capability-skill slugs that should be activated for the calling agent
    # when this skill is loaded via `use-skill`. Lets a knowledge skill bring
    # its own tools into the agent's toolset on demand instead of forcing the
    # agent's YAML to declare every potentially-needed tool upfront.
    enables: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        if self.has_handler and self.body.strip():
            return "hybrid"
        if self.has_handler:
            return "capability"
        return "knowledge"

    def to_catalog_entry(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "version": self.version,
            "kind": self.kind,
            "files": len(self.files),
            "enables": list(self.enables),
        }


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter (between '---' fences) from the markdown body."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    fm_raw = text[4:end].strip()
    body = text[end + 4 :].lstrip("\n")
    try:
        meta = yaml.safe_load(fm_raw) or {}
        if not isinstance(meta, dict):
            meta = {}
    except yaml.YAMLError as e:
        logger.warning("Bad YAML frontmatter: %s", e)
        meta = {}
    return meta, body


def _file_manifest(skill_dir: Path) -> list[dict]:
    """Relative-path manifest of supporting files inside the skill."""
    skip = {"SKILL.md", "handler.py", "schema.yaml"}
    files: list[dict] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if rel in skip or rel.startswith("__pycache__/"):
            continue
        files.append(
            {
                "path": rel,
                "size": path.stat().st_size,
                "sandbox_path": f"/skills/{skill_dir.name}/{rel}",
            }
        )
    return files


def _load_skill_dir(skill_dir: Path) -> Skill | None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    try:
        text = skill_md.read_text()
    except Exception as e:
        logger.warning("Skill %s unreadable: %s", skill_dir.name, e)
        return None

    meta, body = _parse_frontmatter(text)
    has_handler = (skill_dir / "handler.py").exists()

    schema: dict = {}
    schema_path = skill_dir / "schema.yaml"
    if schema_path.exists():
        try:
            with open(schema_path) as f:
                schema = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Skill %s schema.yaml unreadable: %s", skill_dir.name, e)

    raw_enables = meta.get("enables") or []
    if isinstance(raw_enables, str):
        raw_enables = [raw_enables]
    enables: list[str] = [str(s).strip() for s in raw_enables if str(s).strip()]

    return Skill(
        slug=skill_dir.name,
        name=str(meta.get("name") or skill_dir.name),
        description=str(meta.get("description", "")),
        when_to_use=str(meta.get("when_to_use") or meta.get("when-to-use") or ""),
        version=str(meta.get("version", "0.1")),
        body=body,
        has_handler=has_handler,
        schema=schema,
        files=_file_manifest(skill_dir),
        enables=enables,
    )


@lru_cache(maxsize=1)
def discover_skills() -> dict[str, Skill]:
    """Walk backend/skills/<slug>/ and return slug -> Skill."""
    out: dict[str, Skill] = {}
    if not _SKILLS_ROOT.exists():
        return out
    for child in sorted(_SKILLS_ROOT.iterdir()):
        if not child.is_dir():
            continue
        skill = _load_skill_dir(child)
        if skill is not None:
            out[skill.slug] = skill
    return out


def get_skill(slug: str) -> Skill:
    skills = discover_skills()
    if slug not in skills:
        raise KeyError(slug)
    return skills[slug]


def get_capability_skills() -> list[Skill]:
    """Skills with handler.py present — surfaced as callable functions."""
    return [s for s in discover_skills().values() if s.has_handler]


def get_knowledge_skills() -> list[Skill]:
    """Skills without handlers — loaded on-demand via the use-skill capability."""
    return [s for s in discover_skills().values() if not s.has_handler]


def list_skills() -> list[dict]:
    """Lightweight catalog of every skill (capability + knowledge)."""
    return [s.to_catalog_entry() for s in discover_skills().values()]


def load_skill(slug: str) -> dict:
    """Return the full SKILL.md body and a manifest of supporting files.

    Used by the `use-skill` capability so an agent can pull a knowledge skill
    into its context on demand.
    """
    skill = get_skill(slug)
    body = skill.body
    if len(body.encode("utf-8")) > _MAX_SKILL_BYTES:
        body = (
            body[:_MAX_SKILL_BYTES]
            + "\n\n…[skill body truncated — read sub-files via execute-code]"
        )
    return {
        "slug": skill.slug,
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "body": body,
        "files": skill.files,
        "sandbox_root": f"/skills/{skill.slug}",
        "enables": list(skill.enables),
    }


def load_handler(slug: str) -> Callable[..., Callable]:
    """Dynamic-import a capability skill's `create_handler` factory."""
    skill = get_skill(slug)
    if not skill.has_handler:
        raise ValueError(f"Skill '{slug}' is knowledge-only (no handler.py)")
    handler_path = _SKILLS_ROOT / slug / "handler.py"
    module_name = f"_skill_handler_{slug.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, handler_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load handler for skill '{slug}'")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "create_handler"):
        raise AttributeError(f"Skill '{slug}' handler.py missing create_handler()")
    return module.create_handler


def get_normalized_specs(slugs: list[str]) -> list[dict]:
    """Return [{name, description, input_schema}] for the given capability slugs.

    This is the provider-neutral spec format. Each provider translates this
    into its native shape:
      Claude  -> MCP server tools (via mcp_bridge)
      OpenAI  -> [{type: function, function: {name, description, parameters}}]
      Gemini  -> [{name, description, parameters}] inside a Tool wrapper
    """
    out = []
    for slug in slugs:
        try:
            skill = get_skill(slug)
        except KeyError:
            logger.warning("Unknown skill in spec request: %s", slug)
            continue
        if not skill.has_handler:
            continue
        out.append(
            {
                "name": skill.slug,
                "description": skill.description,
                "input_schema": skill.schema or {"type": "object", "properties": {}},
            }
        )
    return out


def reset_cache() -> None:
    """Test hook: clear the discovery cache so a fresh filesystem walk runs."""
    discover_skills.cache_clear()
