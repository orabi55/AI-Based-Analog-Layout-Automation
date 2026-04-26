"""
Configuration Utilities
=======================
Provides centralized management for environment variables and API keys.

Functions:
- get_project_root: Returns the root directory of the project.
- get_api_key: Retrieves an API key for a specified provider.
- set_api_key: Sets an API key for a specified provider in the environment.
"""
import os
from pathlib import Path


def get_project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).resolve().parents[2]


def get_api_key(provider: str) -> str:
    """Get API key for a provider from environment."""
    key_map = {
        "gemini": "GEMINI_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_var = key_map.get(provider.lower(), f"{provider.upper()}_API_KEY")
    return os.environ.get(env_var, "")


def set_api_key(provider: str, key: str):
    """Set API key in environment."""
    key_map = {
        "gemini": "GEMINI_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_var = key_map.get(provider.lower(), f"{provider.upper()}_API_KEY")
    os.environ[env_var] = key
