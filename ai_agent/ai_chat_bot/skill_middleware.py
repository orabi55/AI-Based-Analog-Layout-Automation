"""
Skill middleware for placement prompt augmentation.

On init, scans ai_agent/SKILLS and reads only markdown frontmatter to build a
skill index. Skill bodies are loaded on demand using the load_skills tool.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.tools import StructuredTool
from typing import Callable


class SkillMiddleware:
    """Inject task-specific placement skills into system prompts."""

    def __init__(self, skills_dir: Optional[Path] = None):
        default_dir = Path(__file__).resolve().parents[1] / "SKILLS"
        self.skills_dir = skills_dir or default_dir
        self.skill_index = self._scan_skill_frontmatter()

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

    def _scan_skill_frontmatter(self) -> Dict[str, Dict[str, object]]:
        index: Dict[str, Dict[str, object]] = {}

        if not self.skills_dir.is_dir():
            return index

        for skill_file in sorted(self.skills_dir.glob("*.md")):
            frontmatter = self._read_frontmatter_only(skill_file)
            if not frontmatter:
                continue

            skill_id = str(frontmatter.get("id", skill_file.stem)).strip()
            if not skill_id:
                continue

            name = str(frontmatter.get("name", skill_id)).strip()
            description = str(frontmatter.get("description", "")).strip()
            raw_keywords = frontmatter.get("keywords", [])
            if not isinstance(raw_keywords, list):
                raw_keywords = [raw_keywords] if raw_keywords else []

            keywords = [str(k).strip().lower() for k in raw_keywords if str(k).strip()]

            index[skill_id] = {
                "id": skill_id,
                "name": name,
                "description": description,
                "keywords": keywords,
                "file_path": skill_file,
            }

        return index

    def list_skills(self) -> List[Dict[str, str]]:
        """Return frontmatter-only skill metadata for agent planning."""
        cards: List[Dict[str, str]] = []
        for skill_id, meta in self.skill_index.items():
            cards.append(
                {
                    "id": skill_id,
                    "name": str(meta.get("name", skill_id)),
                    "description": str(meta.get("description", "")),
                    "keywords": ", ".join(meta.get("keywords", []))
                    if isinstance(meta.get("keywords", []), list)
                    else "",
                }
            )
        return cards

    @staticmethod
    def _strip_frontmatter(markdown_text: str) -> str:
        lines = markdown_text.splitlines()
        if not lines or lines[0].strip() != "---":
            return markdown_text.strip()

        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1 :]).strip()

        return markdown_text.strip()

    def detect_skills(
        self,
        user_message: str = "",
        strategy_result: str = "",
        constraint_text: str = "",
    ) -> List[str]:
        """Detect relevant skill keys from current request context."""
        haystack = " ".join(
            [
                str(user_message or ""),
                str(strategy_result or ""),
                str(constraint_text or ""),
            ]
        ).lower()

        selected: List[str] = []
        for skill_id, meta in self.skill_index.items():
            keywords = meta.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []

            if any(str(keyword).lower() in haystack for keyword in keywords if str(keyword).strip()):
                selected.append(skill_id)
                continue

            skill_name = str(meta.get("name", "")).lower().strip()
            if skill_name and skill_name in haystack:
                selected.append(skill_id)

        return selected

    def call_tool(self, tool_name: str, **kwargs):
        """Simple middleware tool dispatcher."""
        if tool_name == "load_skills":
            skill_ids = kwargs.get("skill_ids", [])
            return self.load_skills(skill_ids)
        raise ValueError(f"Unsupported tool: {tool_name}")

    @staticmethod
    def _parse_skill_ids_input(skill_ids: str) -> List[str]:
        raw = str(skill_ids or "").strip()
        if not raw:
            return []
        return [p.strip() for p in raw.split(",") if p.strip()]

    def load_skills(self, skill_ids: List[str]) -> Dict[str, Dict[str, str]]:
        """Tool: load full markdown skill cards only when required."""
        unique_ids = [sid for sid in dict.fromkeys(skill_ids) if sid in self.skill_index]
        if unique_ids:
            print(f"[SKILL_MIDDLEWARE] load_skills tool called: {', '.join(unique_ids)}", flush=True)

        loaded: Dict[str, Dict[str, str]] = {}
        for skill_id in unique_ids:
            meta = self.skill_index.get(skill_id, {})
            file_path = meta.get("file_path")
            if not isinstance(file_path, Path) or not file_path.is_file():
                continue

            try:
                raw_markdown = file_path.read_text(encoding="utf-8")
            except OSError:
                continue

            loaded[skill_id] = {
                "name": str(meta.get("name", skill_id)),
                "description": str(meta.get("description", "")),
                "content": self._strip_frontmatter(raw_markdown),
            }

        return loaded

    def load_skills_tool(self, skill_ids: str = "") -> str:
        """Tool endpoint for ReAct agent: accepts comma-separated skill IDs."""
        parsed_ids = self._parse_skill_ids_input(skill_ids)
        loaded = self.load_skills(parsed_ids)
        if not loaded:
            return "No skills were loaded. Use valid IDs from the skill catalog."

        blocks: List[str] = []
        for sid in parsed_ids:
            card = loaded.get(sid)
            if not card:
                continue

            name = str(card.get("name", sid)).strip() or sid
            description = str(card.get("description", "")).strip()
            content = str(card.get("content", "")).strip()
            lines = [f"## {sid} | {name}"]
            if description:
                lines.append(f"Description: {description}")
            if content:
                lines.append(content)
            blocks.append("\n".join(lines).strip())

        if not blocks:
            return "No skills were loaded. Use valid IDs from the skill catalog."

        return "\n\n".join(blocks)

    def get_react_tools(self) -> List[StructuredTool]:
        """Expose middleware tools for ReAct agent execution."""
        load_skills_tool = StructuredTool.from_function(
            func=self.load_skills_tool,
            name="load_skills",
            description=(
                "Load full skill cards by ID from the skill catalog. "
                "Input is a comma-separated string of IDs, e.g. "
                "'common_centroid,interdigitated'."
            ),
        )
        return [load_skills_tool]

    def build_skill_catalog_block(self) -> str:
        """Build compact catalog from frontmatter metadata only."""
        skills = self.list_skills()
        if not skills:
            return ""

        lines = [
            "AVAILABLE SKILLS CATALOG (frontmatter index):",
            "Use the load_skills tool to load full details only when needed.",
        ]
        for card in skills:
            sid = card.get("id", "")
            name = card.get("name", "")
            desc = card.get("description", "")
            kws = card.get("keywords", "")
            lines.append(f"- {sid} | {name}")
            if desc:
                lines.append(f"  description: {desc}")
            if kws:
                lines.append(f"  keywords: {kws}")

        return "\n".join(lines).strip()

    def build_react_system_prompt(self, base_prompt: str) -> str:
        """Inject ReAct skill-tool instructions and frontmatter catalog."""
        catalog_block = self.build_skill_catalog_block()
        if not catalog_block:
            return base_prompt

        react_block = (
            "\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "REACT SKILL TOOL INSTRUCTIONS\n"
            "1) Inspect the skill catalog below.\n"
            "2) Decide which skills are needed for this placement task.\n"
            "3) Call the load_skills tool with comma-separated IDs only for needed skills.\n"
            "4) Use loaded skill content to produce final [CMD] output.\n"
            "5) Do not assume full skill content without calling load_skills first.\n"
            "═══════════════════════════════════════════════════════════════════════════════\n\n"
            f"{catalog_block}\n"
        )
        return f"{base_prompt.rstrip()}\n\n{react_block}"

    def build_skill_block(self, skill_keys: List[str]) -> str:
        """Build the injected skill section for the system prompt."""
        if not skill_keys:
            return ""

        loaded_skills = self.call_tool("load_skills", skill_ids=skill_keys)
        cards: List[str] = []
        for skill_id in skill_keys:
            card = loaded_skills.get(skill_id)
            if not card:
                continue

            label = str(card.get("name", skill_id)).strip() or skill_id
            description = str(card.get("description", "")).strip()
            content = str(card.get("content", "")).strip()

            card_lines = [f"### {label}"]
            if description:
                card_lines.append(f"Description: {description}")
            if content:
                card_lines.append(content)

            cards.append("\n".join(card_lines).strip())

        if not cards:
            return ""

        return (
            "\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
            "INJECTED TASK SKILLS (SKILL MIDDLEWARE)\n"
            "Use these cards as mandatory mode-specific guidance for this run.\n"
            "If a card conflicts with generic text, prefer the card for that mode.\n"
            "═══════════════════════════════════════════════════════════════════════════════\n\n"
            + "\n\n".join(cards)
        )

    def build_prompt_with_skills(
        self,
        base_prompt: str,
        user_message: str = "",
        strategy_result: str = "",
        constraint_text: str = "",
    ) -> Tuple[str, List[str]]:
        """Return prompt augmented with relevant skill cards and selected keys."""
        selected = self.detect_skills(
            user_message=user_message,
            strategy_result=strategy_result,
            constraint_text=constraint_text,
        )

        skill_block = self.build_skill_block(selected)
        if not skill_block:
            return base_prompt, []

        merged_prompt = f"{base_prompt.rstrip()}\n\n{skill_block}\n"
        return merged_prompt, selected
