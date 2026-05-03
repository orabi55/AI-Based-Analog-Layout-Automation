from pathlib import Path
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import SystemMessage
from typing import Callable

import yaml
from langchain.tools import tool

class SkillMiddleware(AgentMiddleware):
    """Middleware that discovers skills on the filesystem and provides
    them to the agent via progressive disclosure.

    On init: scans skills_dir for SKILL.md files, reads frontmatter.
    On model call: appends skill catalog to system prompt.
    Provides: load_skill tool for on-demand loading.
    """

    def __init__(self, skills_dir: str | Path):
        super().__init__()
        self.skills_dir = Path(skills_dir)
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
                if s["name"] == skill_name:
                    text = Path(s["path"]).read_text(encoding='utf-8')
                    if text.startswith("---"):
                        _, _, body = text.split("---", 2)
                        content = body.strip()
                    else:
                        content = text.strip()
                    return f"Loaded skill: {skill_name}\n\n{content}"

            available = ", ".join(s["name"] for s in registry)
            return f"Skill '{skill_name}' not found. Available skills: {available}"

        self._load_skill_tool = load_skill

    @property
    def tools(self):
        return [self._load_skill_tool]

    def _scan_skills(self) -> list[dict]:
        """Scan skills directory, read only YAML frontmatter."""
        skills = []
        for skill_md in sorted(self.skills_dir.rglob("*.md")):
            text = skill_md.read_text(encoding='utf-8')
            if text.startswith("---"):
                _, frontmatter, _ = text.split("---", 2)
                meta = yaml.safe_load(frontmatter)
                skills.append({
                    "name": meta["name"],
                    "description": meta["description"],
                    "path": str(skill_md),
                })
        return skills

    def _build_catalog(self) -> str:
        """Build the skill catalog string for system prompt injection."""
        lines = [f"- **{s['name']}**: {s['description']}" for s in self.registry]
        return "\n".join(lines)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Inject skill catalog into system prompt before every model call."""
        skills_addendum = (
            f"\n\n## Available Skills\n\n{self.skills_prompt}\n\n"
            "Before starting each phase of your work, load the relevant skill "
            "using the load_skill tool. Skills contain expert strategies and "
            "step-by-step guidelines you should follow. "
            "Don't skip loading a skill just because the task seems familiar — "
            "the skill may contain important details you'd otherwise miss.\n\n"
            "Some skills reference additional files in their directory — "
            "you can read those with read_file for deeper detail."
        )

        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": skills_addendum}
        ]
        new_system_message = SystemMessage(content=new_content)
        modified_request = request.override(system_message=new_system_message)

        return handler(modified_request)