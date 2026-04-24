"""
Centralized logging configuration for the AI-Based Analog Layout Automation project.

Usage:
    from config.logging_config import configure_logging
    configure_logging()  # Call once at application startup (in main.py)

This sets up:
  - Console handler with human-readable format
  - Optional file handler (set LOG_FILE env var)
  - Debug-level for project modules, Warning for third-party libraries
"""

import os
import logging
import sys

_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def configure_logging(level: str | None = None) -> None:
    """Configure the root logger for the entire application.

    Parameters
    ----------
    level : str, optional
        Override log level (e.g. "DEBUG", "INFO").  If not provided,
        reads from the ``LOG_LEVEL`` environment variable, defaulting
        to ``INFO``.
    """
    root = logging.getLogger()
    if root.handlers:
        # Already configured — avoid duplicate handlers on re-entry
        return

    effective_level = level or os.getenv("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, effective_level, logging.INFO))

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, effective_level, logging.INFO))
    console.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    root.addHandler(console)

    # Optional file handler (enabled via LOG_FILE env var)
    log_file = os.getenv("LOG_FILE")
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(fh)

    # Quieten noisy third-party loggers
    for noisy in (
        "httpx", "httpcore", "urllib3", "asyncio",
        "langchain", "langgraph", "google", "grpc",
        "pydantic", "chromadb", "sentence_transformers",
        # Suppress Google GenAI SDK noise (e.g. "AFC is enabled with max remote calls: 10")
        "google_genai", "google.genai", "google.auth", "google.api_core",
        # Suppress OpenAI/Alibaba SDK noise
        "openai", "dashscope",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
