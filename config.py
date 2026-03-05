"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass
class Config:
    """All agent configuration in one place."""

    openrouter_api_key: str
    tavily_api_key: str
    model_name: str
    base_url: str
    langsmith_api_key: str
    langsmith_project: str
    max_iterations: int = 3
    max_web_searches: int = 3
    request_timeout: int = 30

    @classmethod
    def from_env(cls) -> Config:
        """Load and validate config from environment variables.

        Raises:
            ConfigError: If required variables are missing.
        """
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ConfigError(
                "Missing required API key: set OPENROUTER_API_KEY or OPENAI_API_KEY"
            )

        tavily_key = os.getenv("TAVILY_API_KEY", "")

        # Support legacy LANGCHAIN_* aliases
        langsmith_key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY", "")
        langsmith_project = (
            os.getenv("LANGSMITH_PROJECT")
            or os.getenv("LANGCHAIN_PROJECT", "self-reflection-agent")
        )

        # Auto-enable tracing when key is present
        if langsmith_key:
            os.environ.setdefault("LANGSMITH_API_KEY", langsmith_key)
            os.environ.setdefault("LANGSMITH_PROJECT", langsmith_project)
            os.environ.setdefault("LANGSMITH_TRACING", "true")

        return cls(
            openrouter_api_key=api_key,
            tavily_api_key=tavily_key,
            model_name=os.getenv("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b:free"),
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            langsmith_api_key=langsmith_key,
            langsmith_project=langsmith_project,
        )
