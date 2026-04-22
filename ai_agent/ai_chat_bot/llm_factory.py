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

# Default timeout in seconds for LLM API calls
_DEFAULT_TIMEOUT = 120


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

    print(f"\n[LLM_FACTORY] ┌─ Initializing LLM", flush=True)
    print(f"[LLM_FACTORY] │  Provider : {selected_model}", flush=True)
    print(f"[LLM_FACTORY] │  Weight   : {task_weight}", flush=True)

    t_start = time.time()

    if selected_model == "Gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        #model_name = "gemini-2.5-pro" if task_weight == "heavy" else "gemini-2.5-flash"
        model_name = "gemma-4-31b-it"  
        print(f"[LLM_FACTORY] │  Model    : {model_name}", flush=True)
        print(f"[LLM_FACTORY] │  API Key  : {'***' + os.environ.get('GEMINI_API_KEY', '???')[-4:]}", flush=True)
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0.4,
            timeout=_DEFAULT_TIMEOUT,
        )

    elif selected_model == "Alibaba":
        from langchain_openai import ChatOpenAI
        model_name = "qwen-max" if task_weight == "heavy" else "qwen-plus"
        alibaba_key = os.getenv("ALIBABA_API_KEY", "")
        print(f"[LLM_FACTORY] │  Model    : {model_name}", flush=True)
        print(f"[LLM_FACTORY] │  API Key  : {'***' + alibaba_key[-4:] if alibaba_key else '⚠ MISSING'}", flush=True)
        llm = ChatOpenAI(
            api_key=alibaba_key,
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            model=model_name,
            temperature=0.4,
            timeout=_DEFAULT_TIMEOUT,
        )

    elif selected_model == "VertexGemini":
        from langchain_google_vertexai import ChatVertexAI
        model_name = "gemini-2.5-pro" if task_weight == "heavy" else "gemini-2.5-flash"
        project_id = os.getenv("VERTEX_PROJECT_ID", "")
        # gemini-2.5-pro requires location="global"
        if "pro" in model_name:
            location = "global"
        else:
            location = os.getenv("VERTEX_LOCATION", "us-central1")
        print(f"[LLM_FACTORY] │  Model    : {model_name}", flush=True)
        print(f"[LLM_FACTORY] │  Project  : {project_id or '⚠ MISSING'}", flush=True)
        print(f"[LLM_FACTORY] │  Location : {location}", flush=True)
        llm = ChatVertexAI(
            project=project_id,
            location=location,
            model_name=model_name,
            temperature=0.4,
            timeout=_DEFAULT_TIMEOUT,
        )

    elif selected_model == "VertexClaude":
        from langchain_google_vertexai import ChatVertexAI
        model_name = "claude-3-5-sonnet-v2@20241022" if task_weight == "heavy" else "claude-3-5-sonnet@20240620"
        project_id = os.getenv("VERTEX_PROJECT_ID", "")
        location = os.getenv("VERTEX_LOCATION", "us-central1")
        print(f"[LLM_FACTORY] │  Model    : {model_name}", flush=True)
        print(f"[LLM_FACTORY] │  Project  : {project_id or '⚠ MISSING'}", flush=True)
        print(f"[LLM_FACTORY] │  Location : {location}", flush=True)
        llm = ChatVertexAI(
            project=project_id,
            location=location,
            model_name=model_name,
            temperature=0.4,
            timeout=_DEFAULT_TIMEOUT,
        )

    else:
        from langchain_google_genai import ChatGoogleGenerativeAI
        print(f"[LLM_FACTORY] │  ⚠ Unknown provider '{selected_model}', falling back to gemini-2.5-flash", flush=True)
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0.4,
            timeout=_DEFAULT_TIMEOUT,
        )

    elapsed = time.time() - t_start
    print(f"[LLM_FACTORY] └─ Ready ({elapsed:.2f}s)\n", flush=True)
    return llm
