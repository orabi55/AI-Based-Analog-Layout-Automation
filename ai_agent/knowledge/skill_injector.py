"""
Skill Injector Middleware
=========================
Manages the injection of layout skills into agent prompts, providing
progressive disclosure of expert knowledge and strategies.

Public API
----------
- SkillMiddleware(skills_dir)          — construct, scans skills on init
- .augment_system_prompt(base)         — returns base + skill catalog appended
- .build_system_prompt_addendum()      — returns the catalog addendum string only
- .build_skill_catalog_block()         — compact catalog for inline prompt use
- .tool_dicts                          — List[dict] for ReAct-style executors
- .list_skills()                       — frontmatter metadata only, for planning

Internal helpers
----------------
- _strip_quotes
- _parse_frontmatter_lines
- _read_frontmatter_only
- _scan_skills
- _build_catalog
- _make_load_skill_tool_dict

Removed from previous version
------------------------------
- @tool-decorated load_skill (LangChain StructuredTool) — incompatible with
  the plain-dict ReAct executor used by _invoke_react_agent_with_retry.
  The plain-dict version in tool_dicts is the single source of truth.
- .tools property — was returning LC objects; callers must use .tool_dicts.
- .get_react_tools() — alias for the removed .tools property.
- .get_react_tool_dicts() — redundant alias for .tool_dicts.
- .build_react_system_prompt() — redundant alias for .augment_system_prompt().
- .skill_index property — unused externally and internally.
"""

from pathlib import Path
from typing import Dict, List, Optional


class SkillMiddleware:
    """Discovers skills on the filesystem and injects them into agent prompts
    via progressive disclosure.

    Init
    ----
    Scans ``skills_dir`` for ``*.md`` (flat) and ``*/SKILL.md`` (nested) files.
    Only YAML frontmatter is read at scan time; full skill bodies are loaded
    on demand when the agent calls the ``load_skill`` tool during a ReAct loop.

    Usage in a node
    ---------------
    ::

        mw = SkillMiddleware()                          # or SkillMiddleware(path)
        system_prompt = mw.augment_system_prompt(base)  # inject catalog
        tools = mw.tool_dicts                           # plain dicts for ReAct

    Skill file requirements
    -----------------------
    Every skill file must begin with a YAML frontmatter block containing at
    minimum ``id`` and ``name``.  A ``description`` field is strongly
    recommended — without it the catalog entry will have no description and the
    agent cannot decide whether to load the skill.

    Example::

        ---
        id: common_centroid
        name: Common-Centroid Matching
        description: Finger sequencing for matched devices using gradient cancellation.
        ---
        ... skill body ...
    """

    CATALOG_ADDENDUM_TEMPLATE = (
        "\n\n## Available Skills\n\n"
        "{catalog}\n\n"
        "Before each phase of your work, call `load_skill` with the relevant "
        "skill name (use the `id` field). Skills encode expert heuristics and "
        "step-by-step strategies — always load before acting, even on familiar "
        "tasks.\n"
        "Some skills reference sibling files; use `read_file` to load those too."
    )

    def __init__(self, skills_dir: Optional[Path] = None):
        default_dir = Path(__file__).resolve().parents[1] / "SKILLS"
        self.skills_dir = Path(skills_dir) if skills_dir else default_dir
        self.registry: List[Dict] = self._scan_skills()
        # Pre-built catalog string — reused by both prompt methods.
        self._catalog_str: str = self._build_catalog()
        # Single tool dict, built once and reused via the property.
        self._tool_dict: Dict[str, object] = self._make_load_skill_tool_dict()

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def tool_dicts(self) -> List[Dict[str, object]]:
        """Plain tool dicts for ReAct-style executors.

        Each dict has the shape::

            {"name": str, "description": str, "function": Callable}

        This is the *only* tool surface exposed by SkillMiddleware.
        Do **not** use a LangChain ``@tool``-decorated object here — the
        ReAct executor in ``_invoke_react_agent_with_retry`` expects plain dicts.
        """
        return [self._tool_dict]

    # ── Prompt augmentation ─────────────────────────────────────────────────

    def build_system_prompt_addendum(self) -> str:
        """Return the skill catalog addendum string for prompt injection.

        Returns an empty string when no skills were found so callers can
        safely concatenate without adding blank sections.
        """
        if not self._catalog_str:
            return ""
        return self.CATALOG_ADDENDUM_TEMPLATE.format(catalog=self._catalog_str)

    def augment_system_prompt(self, base_prompt: str) -> str:
        """Return ``base_prompt`` with the skill catalog appended.

        This is the primary entry point for prompt augmentation.  Call it
        once per invocation in the node's middleware loop::

            system_prompt = middleware.augment_system_prompt(system_prompt)
        """
        addendum = self.build_system_prompt_addendum()
        if not addendum:
            return base_prompt
        return f"{base_prompt.rstrip()}{addendum}"

    def build_skill_catalog_block(self) -> str:
        """Return a compact, inline-safe catalog block.

        Suitable for embedding directly inside a larger prompt section when
        the full ``CATALOG_ADDENDUM_TEMPLATE`` wrapper is not wanted.
        Uses the same pre-built ``_catalog_str`` so there is no duplicate
        formatting logic.
        """
        if not self._catalog_str:
            return ""
        lines = [
            "AVAILABLE SKILLS CATALOG:",
            "Use the load_skill tool (by skill id) to load full details.",
            self._catalog_str,
        ]
        return "\n".join(lines).strip()

    def list_skills(self) -> List[Dict[str, str]]:
        """Return frontmatter-only metadata for all discovered skills.

        Useful for agent planning steps that need to survey available skills
        without loading full skill bodies.
        """
        return [
            {
                "id": s["id"],
                "name": s["name"],
                "description": s["description"],
            }
            for s in self.registry
        ]

    # ── Internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _strip_quotes(value: str) -> str:
        """Remove surrounding single or double quotes from a string value."""
        val = str(value).strip()
        if (
            len(val) >= 2
            and (
                (val[0] == '"' and val[-1] == '"')
                or (val[0] == "'" and val[-1] == "'")
            )
        ):
            return val[1:-1].strip()
        return val

    @classmethod
    def _parse_frontmatter_lines(cls, lines: List[str]) -> Dict[str, object]:
        """Fallback line-by-line YAML parser used when PyYAML is unavailable.

        Handles scalar values, inline lists (``[a, b]``), and indented list
        items (``- item``).  Nested mappings are not supported — use PyYAML
        for those.
        """
        parsed: Dict[str, object] = {}
        active_list_key: Optional[str] = None

        for raw_line in lines:
            line = raw_line.rstrip("\n")
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                continue

            # Indented list item — only attach if we have an active list key.
            if stripped.startswith("- ") and active_list_key:
                parsed.setdefault(active_list_key, [])
                if isinstance(parsed[active_list_key], list):
                    parsed[active_list_key].append(
                        cls._strip_quotes(stripped[2:].strip())
                    )
                continue

            if ":" not in stripped:
                # Could be a nested mapping key without a value; reset list tracking.
                active_list_key = None
                continue

            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()

            if not key:
                continue

            if value == "":
                # Key with no inline value → start of an indented list.
                parsed[key] = []
                active_list_key = key
                continue

            active_list_key = None

            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                parsed[key] = (
                    []
                    if not inner
                    else [
                        cls._strip_quotes(v.strip())
                        for v in inner.split(",")
                        if v.strip()
                    ]
                )
            else:
                parsed[key] = cls._strip_quotes(value)

        return parsed

    @classmethod
    def _read_frontmatter_only(cls, file_path: Path) -> Dict[str, object]:
        """Read and parse only the YAML frontmatter block from a markdown file.

        Returns an empty dict when the file has no frontmatter or cannot be
        read.  Attempts PyYAML first; falls back to the line parser if PyYAML
        is unavailable or raises a parse error.
        """
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

            frontmatter_text = "".join(fm_lines)

            try:
                import yaml  # type: ignore

                loaded = yaml.safe_load(frontmatter_text)
                if isinstance(loaded, dict):
                    return {k: v for k, v in loaded.items() if k is not None}
                # yaml returned something unexpected; fall through.
            except Exception as yaml_exc:  # noqa: BLE001
                # Log at debug level if a logger is available; not fatal.
                import sys
                print(
                    f"[SkillMiddleware] yaml.safe_load failed for {file_path}: "
                    f"{yaml_exc} — falling back to line parser",
                    file=sys.stderr,
                )

            return cls._parse_frontmatter_lines(fm_lines)

        except OSError:
            return {}

    def _scan_skills(self) -> List[Dict]:
        """Scan ``skills_dir`` and return a registry of skill metadata dicts.

        Supports two directory layouts:

        - **Flat**: ``skills_dir/*.md``
        - **Nested**: ``skills_dir/<name>/SKILL.md``

        Flat files are scanned first.  Nested files whose ``id`` duplicates a
        flat entry are skipped.

        Each registry entry is a dict with keys:
        ``id``, ``name``, ``description``, ``path``.
        """
        skills: List[Dict] = []

        if not self.skills_dir.is_dir():
            return skills

        # ── Flat layout ────────────────────────────────────────────────────
        for skill_md in sorted(self.skills_dir.glob("*.md")):
            frontmatter = self._read_frontmatter_only(skill_md)
            if not frontmatter:
                continue
            skill_id = str(frontmatter.get("id", skill_md.stem)).strip()
            if not skill_id:
                continue
            name = str(frontmatter.get("name", skill_id)).strip()
            description = str(frontmatter.get("description", "")).strip()
            if not description:
                import sys
                print(
                    f"[SkillMiddleware] WARNING: skill '{skill_id}' has no "
                    f"'description' field — catalog entry will be empty.",
                    file=sys.stderr,
                )
            skills.append(
                {
                    "id": skill_id,
                    "name": name,
                    "description": description,
                    "path": str(skill_md),
                }
            )

        # ── Nested layout ──────────────────────────────────────────────────
        for skill_md in sorted(self.skills_dir.glob("*/SKILL.md")):
            frontmatter = self._read_frontmatter_only(skill_md)
            if not frontmatter:
                continue
            skill_id = str(frontmatter.get("id", skill_md.parent.name)).strip()
            if not skill_id:
                continue
            if any(s["id"] == skill_id for s in skills):
                continue  # flat entry takes precedence
            name = str(frontmatter.get("name", skill_id)).strip()
            description = str(frontmatter.get("description", "")).strip()
            if not description:
                import sys
                print(
                    f"[SkillMiddleware] WARNING: skill '{skill_id}' has no "
                    f"'description' field — catalog entry will be empty.",
                    file=sys.stderr,
                )
            skills.append(
                {
                    "id": skill_id,
                    "name": name,
                    "description": description,
                    "path": str(skill_md),
                }
            )

        return skills

    def _build_catalog(self) -> str:
        """Format the skill registry into the catalog string injected into prompts.

        Format: ``- **<name>** (id: `<id>`): <description>``
        """
        lines = [
            f"- **{s['name']}** (id: `{s['id']}`): {s['description']}"
            for s in self.registry
        ]
        return "\n".join(lines)

    def _make_load_skill_tool_dict(self) -> Dict[str, object]:
        """Build and return the ``load_skill`` tool as a plain dict.

        The dict shape ``{"name", "description", "function"}`` matches what
        ``_invoke_react_agent_with_retry`` expects.  The closure captures the
        registry snapshot at construction time so later registry mutations
        (if any) do not affect an in-flight agent.
        """
        registry = self.registry  # snapshot for closure

        def load_skill(skill_name: str) -> str:
            """Load the full body of a skill by name or id.

            Strips the YAML frontmatter block before returning so the agent
            receives only the actionable skill content.
            """
            for s in registry:
                if s["name"] == skill_name or s["id"] == skill_name:
                    text = Path(s["path"]).read_text(encoding="utf-8")
                    if text.startswith("---"):
                        _, _, body = text.split("---", 2)
                        return f"[Skill: {skill_name}]\n\n{body.strip()}"
                    return f"[Skill: {skill_name}]\n\n{text.strip()}"

            available = ", ".join(s["id"] for s in registry)
            return (
                f"Skill '{skill_name}' not found. "
                f"Available skill ids: {available}"
            )

        return {
            "name": "load_skill",
            "description": (
                "Load the full content of a named placement skill into context. "
                "Call this before each placement phase to get expert strategies "
                "and heuristics. Identify the skill by its id or name. "
                "Args: skill_name (str)."
            ),
            "function": load_skill,
        }