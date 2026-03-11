"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


# ---------------------------------------------------------------------------
# Model name resolution
#
# Priority (highest → lowest) for any given node:
#   1. <AGENT>_<NODE>_MODEL  e.g. RESEARCH_PLANNER_MODEL
#   2. <AGENT>_MODEL         e.g. RESEARCH_MODEL
#   3. MODEL_NAME            global fallback
# ---------------------------------------------------------------------------

def resolve_model(*env_vars: str, fallback: str) -> str:
    """Return the first non-empty value among env_vars, or fallback."""
    for var in env_vars:
        val = os.getenv(var, "").strip()
        if val:
            return val
    return fallback


@dataclass
class Config:
    """All agent configuration in one place."""

    openrouter_api_key: str
    tavily_api_key: str
    base_url: str
    langsmith_api_key: str
    langsmith_project: str
    request_timeout: int = 30

    # ── Global default ──────────────────────────────────────────────────────
    model_name: str = "nvidia/nemotron-3-nano-30b-a3b:free"

    # ── duckling (date parsing) ─────────────────────────────────────────────
    duckling_url: str = "http://localhost:8000"

    # ── self_reflection_agent (v1) node models ───────────────────────────
    reflection_v1_search_decision_model: str = ""
    reflection_v1_generate_model: str = ""
    reflection_v1_reflect_model: str = ""

    # ── self_reflection_agent_v2 node models ────────────────────────────
    reflection_v2_generate_model: str = ""
    reflection_v2_reflect_model: str = ""

    # ── research_agent node models ──────────────────────────────────────
    research_planner_model: str = ""
    research_synthesizer_model: str = ""
    research_filter_model: str = ""
    research_topic_extractor_model: str = ""
    research_keyword_expander_model: str = ""
    research_query_generator_model: str = ""
    research_embedding_model: str = "BAAI/bge-large-en-v1.5"

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

        if langsmith_key:
            os.environ.setdefault("LANGSMITH_API_KEY", langsmith_key)
            os.environ.setdefault("LANGSMITH_PROJECT", langsmith_project)

        global_model = os.getenv("MODEL_NAME", "nvidia/nemotron-3-nano-30b-a3b:free")

        return cls(
            openrouter_api_key=api_key,
            tavily_api_key=tavily_key,
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            langsmith_api_key=langsmith_key,
            langsmith_project=langsmith_project,
            model_name=global_model,
            duckling_url=os.getenv("DUCKLING_URL", "http://localhost:8000"),

            # self_reflection_agent (v1)
            reflection_v1_search_decision_model=resolve_model(
                "REFLECTION_V1_SEARCH_DECISION_MODEL", "REFLECTION_V1_MODEL",
                fallback=global_model,
            ),
            reflection_v1_generate_model=resolve_model(
                "REFLECTION_V1_GENERATE_MODEL", "REFLECTION_V1_MODEL",
                fallback=global_model,
            ),
            reflection_v1_reflect_model=resolve_model(
                "REFLECTION_V1_REFLECT_MODEL", "REFLECTION_V1_MODEL",
                fallback=global_model,
            ),

            # self_reflection_agent_v2
            reflection_v2_generate_model=resolve_model(
                "REFLECTION_V2_GENERATE_MODEL", "REFLECTION_V2_MODEL",
                fallback=global_model,
            ),
            reflection_v2_reflect_model=resolve_model(
                "REFLECTION_V2_REFLECT_MODEL", "REFLECTION_V2_MODEL",
                fallback=global_model,
            ),

            # research_agent
            research_planner_model=resolve_model(
                "RESEARCH_PLANNER_MODEL", "RESEARCH_MODEL",
                fallback=global_model,
            ),
            research_synthesizer_model=resolve_model(
                "RESEARCH_SYNTHESIZER_MODEL", "RESEARCH_MODEL",
                fallback=global_model,
            ),
            research_filter_model=resolve_model(
                "RESEARCH_FILTER_MODEL", "RESEARCH_MODEL",
                fallback=global_model,
            ),
            research_topic_extractor_model=resolve_model(
                "RESEARCH_TOPIC_EXTRACTOR_MODEL", "RESEARCH_PLANNER_MODEL", "RESEARCH_MODEL",
                fallback=global_model,
            ),
            research_keyword_expander_model=resolve_model(
                "RESEARCH_KEYWORD_EXPANDER_MODEL", "RESEARCH_PLANNER_MODEL", "RESEARCH_MODEL",
                fallback=global_model,
            ),
            research_query_generator_model=resolve_model(
                "RESEARCH_QUERY_GENERATOR_MODEL", "RESEARCH_PLANNER_MODEL", "RESEARCH_MODEL",
                fallback=global_model,
            ),
            research_embedding_model=resolve_model(
                "RESEARCH_EMBEDDING_MODEL",
                fallback="BAAI/bge-large-en-v1.5",
            ),
        )

    def log_models(self) -> None:
        """Print active model for every agent node to stderr. Controlled by LOG_MODELS=true."""
        import sys
        if os.getenv("LOG_MODELS", "").strip().lower() != "true":
            return
        print(
            "\n"
            "┌─ Active models ────────────────────────────────────────────┐\n"
            f"│ Global fallback     : {self.model_name}\n"
            "│\n"
            "│ self_reflection_agent (v1)\n"
            f"│   search_decision   : {self.reflection_v1_search_decision_model}\n"
            f"│   generate          : {self.reflection_v1_generate_model}\n"
            f"│   reflect           : {self.reflection_v1_reflect_model}\n"
            "│\n"
            "│ self_reflection_agent_v2\n"
            f"│   generate          : {self.reflection_v2_generate_model}\n"
            f"│   reflect           : {self.reflection_v2_reflect_model}\n"
            "│\n"
            "│ research_agent\n"
            f"│   planner           : {self.research_planner_model}\n"
            f"│   filter            : {self.research_filter_model}\n"
            f"│   synthesizer       : {self.research_synthesizer_model}\n"
            f"│   topic_extractor   : {self.research_topic_extractor_model}\n"
            f"│   keyword_expander  : {self.research_keyword_expander_model}\n"
            f"│   query_generator   : {self.research_query_generator_model}\n"
            f"│   embedding         : {self.research_embedding_model}\n"
            "└────────────────────────────────────────────────────────────┘",
            file=sys.stderr,
            flush=True,
        )
