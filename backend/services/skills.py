"""Skills — packaged capabilities (markdown + scripts) the agent loads on demand.

A skill lives at backend/skills/<skill-name>/ with a SKILL.md file and any
supporting templates/scripts. It's loaded *lazily*: the agent calls
list_available_skills, picks one, then use_skill(name) to read its full body.

This is provider-agnostic — skills are just markdown + filenames the agent
runs via execute_code. The Tier 0.1 LLM factory translates `use_skill` into
each provider's native function-calling shape.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILLS_ROOT = Path(__file__).parent.parent / "skills"

# Maximum bytes of SKILL.md content we will embed in a single use_skill response.
# Larger skills should be split into sub-files referenced from SKILL.md.
_MAX_SKILL_BYTES = 64_000


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md frontmatter (YAML between '---' fences) from the body.

    Returns ({}, text) when the file lacks frontmatter.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    fm_raw = text[4:end].strip()
    body = text[end + 4 :].lstrip("\n")

    meta: dict = {}
    for line in fm_raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip().lower()] = val.strip().strip("'").strip('"')
    return meta, body


@lru_cache(maxsize=1)
def _discover() -> dict[str, Path]:
    """Map skill-name → directory path."""
    if not _SKILLS_ROOT.exists():
        return {}
    out: dict[str, Path] = {}
    for child in sorted(_SKILLS_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "SKILL.md").exists():
            continue
        out[child.name] = child
    return out


def _file_manifest(skill_dir: Path) -> list[dict]:
    """Return a relative-path manifest of supporting files inside the skill."""
    files: list[dict] = []
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if rel == "SKILL.md":
            continue
        files.append(
            {
                "path": rel,
                "size": path.stat().st_size,
                "sandbox_path": f"/skills/{skill_dir.name}/{rel}",
            }
        )
    return files


def list_skills() -> list[dict]:
    """Lightweight catalog: name + description (and `when_to_use` if declared)."""
    catalog: list[dict] = []
    for name, path in _discover().items():
        try:
            text = (path / "SKILL.md").read_text()
        except Exception as e:
            logger.warning("Skill %s unreadable: %s", name, e)
            continue
        meta, _body = _parse_frontmatter(text)
        catalog.append(
            {
                "name": meta.get("name") or name,
                "slug": name,
                "description": meta.get("description", ""),
                "when_to_use": meta.get("when_to_use") or meta.get("when-to-use", ""),
                "version": meta.get("version", "0.1"),
                "files": len(_file_manifest(path)),
            }
        )
    return catalog


def load_skill(slug: str) -> dict:
    """Return the full SKILL.md body and a manifest of supporting files.

    Raises KeyError if the skill is not found.
    """
    discovered = _discover()
    if slug not in discovered:
        raise KeyError(slug)
    path = discovered[slug]
    text = (path / "SKILL.md").read_text()
    meta, body = _parse_frontmatter(text)
    if len(body.encode("utf-8")) > _MAX_SKILL_BYTES:
        body = body[:_MAX_SKILL_BYTES] + "\n\n…[skill body truncated — read sub-files via execute_code]"
    return {
        "slug": slug,
        "name": meta.get("name") or slug,
        "description": meta.get("description", ""),
        "version": meta.get("version", "0.1"),
        "body": body,
        "files": _file_manifest(path),
        "sandbox_root": f"/skills/{slug}",
    }
