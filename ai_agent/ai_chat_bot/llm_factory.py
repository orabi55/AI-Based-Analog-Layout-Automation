"""
ai_agent/ai_chat_bot/llm_factory.py
=====================================
Centralized LangChain model factory — the SINGLE source of truth for
all model instantiation across the entire AI pipeline.

Dynamically picks the optimal model variant based on `task_weight`:
  - "light" → fast/cheap model (flash, plus variants)
  - "heavy" → powerful model (pro, max variants)
"""

import os
import time
from types import SimpleNamespace

# Default timeout in seconds for LLM API calls
_DEFAULT_TIMEOUT = 120


def _placement_steps_only() -> bool:
    return os.environ.get("PLACEMENT_STEPS_ONLY", "0").lower() in (
        "1", "true", "yes"
    )


def _flog(*args, **kwargs) -> None:
    """Log line for LLM factory; suppressed during UI initial placement step-only mode."""
    if _placement_steps_only():
        return
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


def _messages_to_prompt(messages) -> str:
    """Convert LangChain-style messages into a plain prompt string."""
    lines = []
    for msg in messages or []:
        if isinstance(msg, dict):
            role = str(msg.get("role", "user")).strip().upper()
            content = str(msg.get("content", "")).strip()
        else:
            role = str(getattr(msg, "role", "user")).strip().upper()
            content = str(getattr(msg, "content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


class _NativeLLMAdapter:
    """Small adapter that exposes an .invoke(messages) method."""

    def __init__(self, invoker):
        self._invoker = invoker

    def invoke(self, messages):
        text = self._invoker(messages)
        return SimpleNamespace(content=text or "")


def _build_native_gemini(api_key: str, model_name: str):
    from google import genai
    from google.genai import types as gtypes

    client = genai.Client(api_key=api_key)

    def _invoke(messages):
        prompt = _messages_to_prompt(messages)
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=gtypes.GenerateContentConfig(max_output_tokens=65536),
        )
        return (resp.text or "").strip()

    return _NativeLLMAdapter(_invoke)


def _build_native_vertex_gemini(project_id: str, location: str, model_name: str):
    from google import genai
    from google.genai import types as gtypes

    client = genai.Client(vertexai=True, project=project_id, location=location)

    def _invoke(messages):
        prompt = _messages_to_prompt(messages)
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=gtypes.GenerateContentConfig(max_output_tokens=65536),
        )
        return (resp.text or "").strip()

    return _NativeLLMAdapter(_invoke)


def _build_native_alibaba(api_key: str, model_name: str):
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )

    def _invoke(messages):
        wire_messages = []
        for m in messages or []:
            if isinstance(m, dict):
                role = str(m.get("role", "user")).strip().lower()
                content = str(m.get("content", "")).strip()
            else:
                role = str(getattr(m, "role", "user")).strip().lower()
                content = str(getattr(m, "content", "")).strip()
            if role not in ("system", "user", "assistant"):
                role = "user"
            if content:
                wire_messages.append({"role": role, "content": content})
        if not wire_messages:
            wire_messages = [{"role": "user", "content": "Hello"}]
        resp = client.chat.completions.create(
            model=model_name,
            messages=wire_messages,
            max_tokens=8192,
            temperature=0.4,
        )
        return (resp.choices[0].message.content or "").strip()

    return _NativeLLMAdapter(_invoke)


def _build_native_vertex_claude(project_id: str, location: str, model_name: str):
    import anthropic

    client = anthropic.AnthropicVertex(project_id=project_id, region=location)

    def _invoke(messages):
        prompt = _messages_to_prompt(messages)
        resp = client.messages.create(
            model=model_name,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.content[0].text or "").strip()

    return _NativeLLMAdapter(_invoke)


def _resolve_timeout(task_weight: str) -> int:
    env_key = "LLM_TIMEOUT_HEAVY" if task_weight == "heavy" else "LLM_TIMEOUT_LIGHT"
    raw = os.getenv(env_key, str(_DEFAULT_TIMEOUT))
    try:
        timeout = int(raw)
        return timeout if timeout > 0 else _DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


def get_langchain_llm(selected_model: str, task_weight: str = "light"):
    """
    Dynamically instantiate the appropriate LangChain chat model based on the
    user's UI selection and the requested logic weight ('light' vs 'heavy').

    Parameters
    ----------
    selected_model : str
        Provider key: "Gemini" | "Alibaba" | "VertexGemini" | "VertexClaude"
    task_weight : str
        "light" (fast/cheap) or "heavy" (powerful/expensive)

    Returns
    -------
    langchain BaseChatModel instance ready to .invoke()
    """
    # Ensure LangChain finds the Google string
    if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

    _flog(f"\n[LLM_FACTORY] ┌─ Initializing LLM")
    _flog(f"[LLM_FACTORY] │  Provider : {selected_model}")
    _flog(f"[LLM_FACTORY] │  Weight   : {task_weight}")
    request_timeout = _resolve_timeout(task_weight)
    _flog(f"[LLM_FACTORY] │  Timeout  : {request_timeout}s")

    t_start = time.time()

    if selected_model == "Gemini":
        model_name = "gemini-2.5-pro" if task_weight == "heavy" else "gemini-2.5-flash"
        _flog(f"[LLM_FACTORY] │  Model    : {model_name}")
        _flog(f"[LLM_FACTORY] │  API Key  : {'***' + os.environ.get('GEMINI_API_KEY', '')[-4:] if os.environ.get('GEMINI_API_KEY') else '⚠ MISSING'}")
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(
                model=model_name,
                temperature=0.4,
                timeout=request_timeout,
            )
        except ImportError:
            _flog("[LLM_FACTORY] │  LangChain Gemini adapter missing; using native google-genai fallback")
            gemini_key = os.environ.get("GEMINI_API_KEY", "")
            if not gemini_key:
                raise ValueError("GEMINI_API_KEY not set")
            llm = _build_native_gemini(gemini_key, model_name)

    elif selected_model == "Alibaba":
        model_name = "qwen-max" if task_weight == "heavy" else "qwen-plus"
        alibaba_key = os.getenv("ALIBABA_API_KEY", "")
        _flog(f"[LLM_FACTORY] │  Model    : {model_name}")
        _flog(f"[LLM_FACTORY] │  API Key  : {'***' + alibaba_key[-4:] if alibaba_key else '⚠ MISSING'}")
        try:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                api_key=alibaba_key,
                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                model=model_name,
                temperature=0.4,
                timeout=request_timeout,
            )
        except ImportError:
            _flog("[LLM_FACTORY] │  LangChain OpenAI adapter missing; using native OpenAI fallback")
            if not alibaba_key:
                raise ValueError("ALIBABA_API_KEY not set")
            llm = _build_native_alibaba(alibaba_key, model_name)

    elif selected_model == "VertexGemini":
        model_name = "gemini-2.5-pro" if task_weight == "heavy" else "gemini-2.5-flash"
        project_id = os.getenv("VERTEX_PROJECT_ID", "")
        # gemini-2.5-pro requires location="global"
        if "pro" in model_name:
            location = "global"
        else:
            location = os.getenv("VERTEX_LOCATION", "us-central1")
        _flog(f"[LLM_FACTORY] │  Model    : {model_name}")
        _flog(f"[LLM_FACTORY] │  Project  : {project_id or '⚠ MISSING'}")
        _flog(f"[LLM_FACTORY] │  Location : {location}")
        try:
            from langchain_google_vertexai import ChatVertexAI
            llm = ChatVertexAI(
                project=project_id,
                location=location,
                model_name=model_name,
                temperature=0.4,
                timeout=request_timeout,
            )
        except ImportError:
            _flog("[LLM_FACTORY] │  LangChain Vertex adapter missing; using native Vertex Gemini fallback")
            if not project_id:
                raise ValueError("VERTEX_PROJECT_ID not set")
            llm = _build_native_vertex_gemini(project_id, location, model_name)

    elif selected_model == "VertexClaude":
        model_name = "claude-3-5-sonnet-v2@20241022" if task_weight == "heavy" else "claude-3-5-sonnet@20240620"
        project_id = os.getenv("VERTEX_PROJECT_ID", "")
        location = os.getenv("VERTEX_LOCATION", "us-central1")
        _flog(f"[LLM_FACTORY] │  Model    : {model_name}")
        _flog(f"[LLM_FACTORY] │  Project  : {project_id or '⚠ MISSING'}")
        _flog(f"[LLM_FACTORY] │  Location : {location}")
        try:
            from langchain_google_vertexai import ChatVertexAI
            llm = ChatVertexAI(
                project=project_id,
                location=location,
                model_name=model_name,
                temperature=0.4,
                timeout=request_timeout,
            )
        except ImportError:
            _flog("[LLM_FACTORY] │  LangChain Vertex adapter missing; using native Vertex Claude fallback")
            if not project_id:
                raise ValueError("VERTEX_PROJECT_ID not set")
            llm = _build_native_vertex_claude(project_id, location, model_name)

    else:
        _flog(f"[LLM_FACTORY] │  ⚠ Unknown provider '{selected_model}', falling back to gemini-2.5-flash")
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                temperature=0.4,
                timeout=request_timeout,
            )
        except ImportError:
            _flog("[LLM_FACTORY] │  LangChain Gemini adapter missing; using native google-genai fallback")
            gemini_key = os.environ.get("GEMINI_API_KEY", "")
            if not gemini_key:
                raise ValueError("GEMINI_API_KEY not set")
            llm = _build_native_gemini(gemini_key, "gemini-2.5-flash")

    elapsed = time.time() - t_start
    _flog(f"[LLM_FACTORY] └─ Ready ({elapsed:.2f}s)\n")
    return llm
