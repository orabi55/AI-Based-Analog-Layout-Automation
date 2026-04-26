"""
LLM Model Factory
=================
Centralized factory for instantiating LangChain chat models, dynamically 
selecting model variants based on task weight and provider.

Functions:
- _placement_steps_only: Checks if only placement steps should be logged.
- _flog: Internal logging helper for the LLM factory.
- _resolve_timeout: Determines the timeout for LLM API calls.
- get_langchain_llm: Instantiates the appropriate LangChain chat model.
  - Inputs: selected_model (str), task_weight (str)
  - Outputs: LangChain BaseChatModel instance.
"""

import os
import time


# Default timeout in seconds for LLM API calls
_DEFAULT_TIMEOUT = 300


def _placement_steps_only() -> bool:
    return os.environ.get("PLACEMENT_STEPS_ONLY", "0").lower() in (
        "1", "true", "yes",
    )


def _flog(*args, **kwargs) -> None:
    """Log line for LLM factory — writes to placement_live_output.log, never to stdout."""
    text = " ".join(str(a) for a in args)
    try:
        with open("placement_live_output.log", "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


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
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        _flog(f"[LLM_FACTORY] │  Model    : {model_name}")
        _flog(f"[LLM_FACTORY] │  API Key  : {'***' + gemini_key[-4:] if gemini_key else '⚠ MISSING'}")

        if not gemini_key:
            raise ValueError("GEMINI_API_KEY environment variable is missing. Please set it in the AI Model Settings.")

        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0.4,
            timeout=request_timeout,
        )

    elif selected_model == "Alibaba":
        model_name = "qwen-max" if task_weight == "heavy" else "qwen-plus"
        alibaba_key = os.getenv("ALIBABA_API_KEY", "sk-567af8d3cf51494faa346579ba523add")
        _flog(f"[LLM_FACTORY] │  Model    : {model_name}")
        _flog(f"[LLM_FACTORY] │  API Key  : {'***' + alibaba_key[-4:] if alibaba_key else '⚠ MISSING'}")

        if not alibaba_key:
            raise ValueError("ALIBABA_API_KEY environment variable is missing.")

        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            api_key=alibaba_key,
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            model=model_name,
            temperature=0.4,
            timeout=request_timeout,
        )

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

        if not project_id:
            raise ValueError("VERTEX_PROJECT_ID environment variable is missing. Please set it in the AI Model Settings.")

        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            project=project_id,
            location=location,
            model=model_name,
            temperature=0.4,
            timeout=request_timeout,
        )

    elif selected_model == "VertexClaude":
        model_name = "claude-3-5-sonnet-v2@20241022" if task_weight == "heavy" else "claude-3-5-sonnet@20240620"
        project_id = os.getenv("VERTEX_PROJECT_ID", "")
        location = os.getenv("VERTEX_LOCATION", "us-central1")
        _flog(f"[LLM_FACTORY] │  Model    : {model_name}")
        _flog(f"[LLM_FACTORY] │  Project  : {project_id or '⚠ MISSING'}")
        _flog(f"[LLM_FACTORY] │  Location : {location}")

        if not project_id:
            raise ValueError("VERTEX_PROJECT_ID environment variable is missing. Please set it in the AI Model Settings.")

        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            project=project_id,
            location=location,
            model=model_name,
            temperature=0.4,
            timeout=request_timeout,
        )

    else:
        _flog(f"[LLM_FACTORY] │  ⚠ Unknown provider '{selected_model}', falling back to gemini-2.5-flash")
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if not gemini_key:
            raise ValueError("GEMINI_API_KEY environment variable is missing for the fallback model.")

        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0.4,
            timeout=request_timeout,
        )

    elapsed = time.time() - t_start
    _flog(f"[LLM_FACTORY] └─ Ready ({elapsed:.2f}s)\n")
    return llm
