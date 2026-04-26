"""
Skill Injector Middleware
=========================
Manages the injection of layout skills into agent prompts, providing 
progressive disclosure of expert knowledge and strategies.

Functions:
- load_skill (tool): Loads the full content of a skill from a markdown file.
- build_system_prompt_addendum: Builds the skill catalog section for prompt injection.
- augment_system_prompt: Appends the skill catalog to a base system prompt.
- _strip_quotes: Normalizes string values from frontmatter.
- _parse_frontmatter_lines: Parses frontmatter metadata from skill files.
- _read_frontmatter_only: Reads only the frontmatter section of a markdown file.
- _scan_skills: Scans the skills directory to build a registry of metadata.
- _build_catalog: Formats the registry into a human-readable list.
- list_skills: Returns a list of skill metadata for planning.
- get_react_tools: Returns tools for ReAct agent registration.
- build_react_system_prompt: Alias for augment_system_prompt.
- build_skill_catalog_block: Builds a compact catalog block for prompts.
"""

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from langchain_core.tools import tool


class SkillMiddleware:
    """Middleware that discovers skills on the filesystem and provides
    them to the agent via progressive disclosure.

    On init: scans skills_dir for *.md files, reads frontmatter.
    On model call: appends skill catalog to system prompt.
    Provides: load_skill tool for on-demand loading.
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        default_dir = Path(__file__).resolve().parents[1] / "SKILLS"
        self.skills_dir = Path(skills_dir) if skills_dir else default_dir
        self.registry = self._scan_skills()
        self.skills_prompt = self._build_catalog()

        # Create the load_skill tool with access to this middleware's registry
        registry = self.registry  # capture for closure

        @tool
        def load_skill(skill_name: str) -> str:
            """Load the full content of a skill into the agent's context.

            Use this when you need detailed guidance on how to approach
            a specific type of task. Skills contain reasoning strategies,
            heuristics, and guidelines — not actions.

            Args:
                skill_name: The name of the skill to load.
            """
            for s in registry:
                if s["name"] == skill_name or s["id"] == skill_name:
                    text = Path(s["path"]).read_text(encoding="utf-8")
                    if text.startswith("---"):
                        _, _, body = text.split("---", 2)
                        content = body.strip()
                    else:
                        content = text.strip()
                    return f"Loaded skill: {skill_name}\n\n{content}"

            available = ", ".join(s["name"] for s in registry)
            return f"Skill '{skill_name}' not found. Available skills: {available}"

        self._load_skill_tool = load_skill

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def tools(self):
        """Tools exposed by this middleware for agent tool registration."""
        return [self._load_skill_tool]

    @property
    def skill_index(self) -> Dict[str, Dict[str, object]]:
        """Legacy-compatible skill index keyed by skill ID."""
        return {s["id"]: s for s in self.registry}

    # ── Prompt augmentation ─────────────────────────────────────────────────

    def build_system_prompt_addendum(self) -> str:
        """Build the skill catalog addendum for system prompt injection."""
        if not self.skills_prompt:
            return ""
        return (
            f"\n\n## Available Skills\n\n{self.skills_prompt}\n\n"
            "Before starting each phase of your work, load the relevant skill "
            "using the load_skill tool. Skills contain expert strategies and "
            "step-by-step guidelines you should follow. "
            "Don't skip loading a skill just because the task seems familiar — "
            "the skill may contain important details you'd otherwise miss.\n\n"
            "Some skills reference additional files in their directory — "
            "you can read those with read_file for deeper detail."
        )

    def augment_system_prompt(self, base_prompt: str) -> str:
        """Return base_prompt with skill catalog appended.

        This is the simple string-based alternative to wrap_model_call
        for non-middleware agent pipelines.
        """
        addendum = self.build_system_prompt_addendum()
        if not addendum:
            return base_prompt
        return f"{base_prompt.rstrip()}{addendum}"

    # ── Internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _strip_quotes(value: str) -> str:
        val = str(value).strip()
        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            return val[1:-1].strip()
        return val

    @classmethod
    def _parse_frontmatter_lines(cls, lines: List[str]) -> Dict[str, object]:
        parsed: Dict[str, object] = {}
        active_list_key: Optional[str] = None

        for raw_line in lines:
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if stripped.startswith("- ") and active_list_key:
                parsed.setdefault(active_list_key, [])
                if isinstance(parsed[active_list_key], list):
                    parsed[active_list_key].append(cls._strip_quotes(stripped[2:].strip()))
                continue

            if ":" not in stripped:
                continue

            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()

            if not key:
                continue

            if value == "":
                parsed[key] = []
                active_list_key = key
                continue

            active_list_key = None

            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if not inner:
                    parsed[key] = []
                else:
                    parsed[key] = [cls._strip_quotes(v.strip()) for v in inner.split(",") if v.strip()]
            else:
                parsed[key] = cls._strip_quotes(value)

        return parsed

    @classmethod
    def _read_frontmatter_only(cls, file_path: Path) -> Dict[str, object]:
        """Read and parse only markdown frontmatter from file."""
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                first = handle.readline()
                if first.strip() != "---":
                    return {}

                fm_lines: List[str] = []
                for line in handle:
                    if line.strip() == "---":
                        break
                    fm_lines.append(line)

            return cls._parse_frontmatter_lines(fm_lines)
        except OSError:
            return {}

    def _scan_skills(self) -> List[Dict]:
        """Scan skills directory, read only YAML frontmatter.

        Supports two layouts:
          - Flat:  skills_dir/*.md
          - Nested: skills_dir/*/SKILL.md
        """
        skills: List[Dict] = []

        if not self.skills_dir.is_dir():
            return skills

        # Flat layout: *.md files directly in skills_dir
        for skill_md in sorted(self.skills_dir.glob("*.md")):
            frontmatter = self._read_frontmatter_only(skill_md)
            if not frontmatter:
                continue
            skill_id = str(frontmatter.get("id", skill_md.stem)).strip()
            if not skill_id:
                continue
            name = str(frontmatter.get("name", skill_id)).strip()
            description = str(frontmatter.get("description", "")).strip()
            skills.append({
                "id": skill_id,
                "name": name,
                "description": description,
                "path": str(skill_md),
            })

        # Nested layout: subdirectory/SKILL.md
        for skill_md in sorted(self.skills_dir.glob("*/SKILL.md")):
            frontmatter = self._read_frontmatter_only(skill_md)
            if not frontmatter:
                continue
            skill_id = str(frontmatter.get("id", skill_md.parent.name)).strip()
            if not skill_id:
                continue
            # Skip if already found via flat layout
            if any(s["id"] == skill_id for s in skills):
                continue
            name = str(frontmatter.get("name", skill_id)).strip()
            description = str(frontmatter.get("description", "")).strip()
            skills.append({
                "id": skill_id,
                "name": name,
                "description": description,
                "path": str(skill_md),
            })

        return skills

    def _build_catalog(self) -> str:
        """Build the skill catalog string for system prompt injection."""
        lines = [f"- **{s['name']}** (id: `{s['id']}`): {s['description']}" for s in self.registry]
        return "\n".join(lines)

    # ── Legacy compatibility methods ────────────────────────────────────────

    def list_skills(self) -> List[Dict[str, str]]:
        """Return frontmatter-only skill metadata for agent planning."""
        return [
            {
                "id": s["id"],
                "name": s["name"],
                "description": s["description"],
            }
            for s in self.registry
        ]

    def get_react_tools(self) -> list:
        """Expose middleware tools for ReAct agent execution.

        Alias for the ``tools`` property for backward compatibility.
        """
        return self.tools

    def build_react_system_prompt(self, base_prompt: str) -> str:
        """Inject skill catalog and load_skill instructions into a system prompt.

        Alias for ``augment_system_prompt`` for backward compatibility.
        """
        return self.augment_system_prompt(base_prompt)

    def build_skill_catalog_block(self) -> str:
        """Build compact catalog from frontmatter metadata only."""
        skills = self.list_skills()
        if not skills:
            return ""

        lines = [
            "AVAILABLE SKILLS CATALOG (frontmatter index):",
            "Use the load_skill tool to load full details only when needed.",
        ]
        for card in skills:
            sid = card.get("id", "")
            name = card.get("name", "")
            desc = card.get("description", "")
            lines.append(f"- {sid} | {name}")
            if desc:
                lines.append(f"  description: {desc}")

        return "\n".join(lines).strip()
