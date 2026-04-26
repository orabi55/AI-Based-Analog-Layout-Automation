"""
Skill Loader
============
Parses structured skill definitions from markdown files and provides a catalog 
for agents to discover and use layout skills.

Functions:
- to_prompt_section (in Skill): Formats a skill as a system prompt section.
- _ensure_loaded (in SkillCatalog): Ensures skills are loaded from disk.
- _resolve_skills_dir (in SkillCatalog): Locates the skills directory.
- _parse_skill_file (in SkillCatalog): Parses an individual markdown skill file.
- _parse_sections (in SkillCatalog): Parses sections within a skill's body.
- load_all (in SkillCatalog): Returns all skills as a text block.
- load_by_id (in SkillCatalog): Retrieves a skill by its ID.
- load_by_trigger (in SkillCatalog): Finds skills matching a keyword.
- list_all (in SkillCatalog): Lists all loaded skills.
- to_prompt_catalog (in SkillCatalog): Returns a summary catalog for prompts.
- inject_for_mode (in SkillCatalog): Injects appropriate skills for a given mode.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Skill:
    """A single layout skill parsed from a SKILLS/*.md file."""
    id: str
    name: str
    trigger: list[str] = field(default_factory=list)
    scope: str = "local"  # "local" | "global"
    priority: int = 5
    body: str = ""
    algorithm: str = ""
    constraints: str = ""
    validation: str = ""
    forbidden: str = ""
    relationship: str = ""

    def to_prompt_section(self) -> str:
        """Return this skill as a system prompt section."""
        sections = []
        sections.append(f"## {self.name} (id: {self.id})")
        if self.constraints:
            sections.append(f"### Constraints\n{self.constraints}")
        if self.algorithm:
            sections.append(f"### Algorithm\n{self.algorithm}")
        if self.validation:
            sections.append(f"### Validation\n{self.validation}")
        if self.forbidden:
            sections.append(f"### Forbidden\n{self.forbidden}")
        return "\n\n".join(sections)


class SkillCatalog:
    """Catalog of all layout skills. Discovers and parses SKILLS/*.md files."""

    _cache: dict[str, Skill] = {}
    _skills_dir: Optional[Path] = None

    @classmethod
    def _ensure_loaded(cls):
        if cls._cache:
            return
        cls._skills_dir = cls._resolve_skills_dir()
        for md_path in sorted(cls._skills_dir.glob("*.md")):
            skill = cls._parse_skill_file(md_path)
            if skill:
                cls._cache[skill.id] = skill

    @classmethod
    def _resolve_skills_dir(cls) -> Path:
        """Find the SKILLS directory relative to this file."""
        return Path(__file__).resolve().parent

    @classmethod
    def _parse_skill_file(cls, path: Path) -> Optional[Skill]:
        """Parse a SKILLS/*.md file into a Skill object."""
        text = path.read_text(encoding="utf-8")
        if not text.strip().startswith("---"):
            return None

        lines = text.splitlines()
        # Skip the opening --- line
        body_start = 1
        while body_start < len(lines) and lines[body_start].strip() == "":
            body_start += 1

        # Collect frontmatter lines until we hit a section separator or body content
        # Frontmatter ends at the first line that starts with a box-drawing char or blank line before sections
        frontmatter_lines = []
        body_lines = []
        in_frontmatter = True
        for i in range(1, len(lines)):
            line = lines[i]
            if in_frontmatter:
                # Empty line might end frontmatter, but look ahead for more key:value
                if line.strip() == "":
                    # Peek ahead: if next non-empty line has key:val, continue frontmatter
                    peek = i + 1
                    while peek < len(lines) and lines[peek].strip() == "":
                        peek += 1
                    if peek < len(lines) and ":" in lines[peek] and not lines[peek].strip().startswith("─"):
                        frontmatter_lines.append(line)
                        continue
                    else:
                        in_frontmatter = False
                        continue
                # Check if this is a section separator (line of ─ characters)
                if line.strip().startswith("\u2500") or line.strip().startswith("-" * 10):
                    in_frontmatter = False
                    continue
                frontmatter_lines.append(line)
            else:
                body_lines.append(line)

        # Parse frontmatter metadata
        meta = {}
        current_key = None
        current_value_lines = []
        for line in frontmatter_lines:
            if line.strip() == "":
                continue
            # Indented lines or list items are continuations
            if line.startswith("  ") or line.startswith("    "):
                if current_key:
                    current_value_lines.append(line.strip())
                continue
            elif ":" in line:
                # Save previous key
                if current_key:
                    meta[current_key] = "\n".join(current_value_lines).strip()
                key, _, val = line.partition(":")
                current_key = key.strip().lower()
                current_value_lines = [val.strip()] if val.strip() else []
            else:
                if current_key:
                    current_value_lines.append(line.strip())
        if current_key:
            meta[current_key] = "\n".join(current_value_lines).strip()

        body = "\n".join(body_lines).strip()

        # Parse sections from body using ──── separators
        sections = cls._parse_sections(body)

        # Parse trigger: could be "keywords: [...]" or multi-line
        trigger = []
        trigger_raw = meta.get("trigger", "")
        # Extract keywords from trigger block
        kw_match = re.search(r'keywords:\s*\[(.*?)\]', trigger_raw, re.DOTALL)
        if kw_match:
            trigger_raw = kw_match.group(1)
        trigger = [t.strip().strip('"\'').lower() for t in re.split(r"[,|]", trigger_raw) if t.strip()]

        return Skill(
            id=meta.get("id", path.stem),
            name=meta.get("name", path.stem.replace("-", " ").title()),
            trigger=trigger,
            scope=meta.get("scope", "local"),
            priority=int(meta.get("priority", 5)),
            body=body,
            algorithm=sections.get("ALGORITHM", ""),
            constraints=sections.get("CONSTRAINTS", ""),
            validation=sections.get("VALIDATION", ""),
            forbidden=sections.get("FORBIDDEN", ""),
            relationship=sections.get("RELATIONSHIP TO OTHER SKILLS", ""),
        )

    @classmethod
    def _parse_sections(cls, body: str) -> dict[str, str]:
        """Parse markdown body into named sections using ─── separators."""
        sections = {}
        current_section = "BODY"
        current_lines = []

        for line in body.splitlines():
            stripped = line.strip()
            # Check for section separator (line of ─ or long ---)
            if stripped and (all(c == "\u2500" for c in stripped) or (len(stripped) > 10 and all(c == "-" for c in stripped))):
                continue
            # Section title follows a separator
            if stripped.isupper() and len(stripped) > 2 and not any(c in stripped for c in " .,;:!?"):
                if current_lines:
                    sections[current_section] = "\n".join(current_lines).strip()
                current_section = stripped
                current_lines = []
            else:
                current_lines.append(line)

        if current_lines:
            sections[current_section] = "\n".join(current_lines).strip()

        return sections

    @classmethod
    def load_all(cls) -> str:
        """All skills as a single text block (backward compatibility)."""
        cls._ensure_loaded()
        blocks = []
        for skill in sorted(cls._cache.values(), key=lambda s: s.priority):
            blocks.append(f"### SKILL: {skill.name} (id: {skill.id})\n{skill.body}")
        return "\n\n".join(blocks)

    @classmethod
    def load_by_id(cls, skill_id: str) -> Optional[Skill]:
        """Get a single skill by ID."""
        cls._ensure_loaded()
        return cls._cache.get(skill_id)

    @classmethod
    def load_by_trigger(cls, keyword: str) -> list[Skill]:
        """Find skills matching a trigger keyword."""
        cls._ensure_loaded()
        keyword = keyword.lower()
        results = []
        for skill in cls._cache.values():
            for t in skill.trigger:
                if keyword in t or t in keyword:
                    results.append(skill)
                    break
        return sorted(results, key=lambda s: s.priority)

    @classmethod
    def list_all(cls) -> list[Skill]:
        """List all loaded skills."""
        cls._ensure_loaded()
        return list(cls._cache.values())

    @classmethod
    def to_prompt_catalog(cls) -> str:
        """Return all skills as a system prompt catalog (summary only)."""
        cls._ensure_loaded()
        lines = ["## Available Layout Skills:"]
        for skill in sorted(cls._cache.values(), key=lambda s: s.priority):
            lines.append(f"- **{skill.name}** (`{skill.id}`): triggers on {', '.join(skill.trigger[:3])}")
        return "\n".join(lines)

    @classmethod
    def inject_for_mode(cls, mode: str) -> str:
        """Inject skills appropriate for the given mode."""
        cls._ensure_loaded()
        if mode == "initial":
            # Full skills for initial placement
            return cls.load_all()
        else:
            # Summary catalog for chat mode (saves tokens)
            return cls.to_prompt_catalog()
