"""
config.py
=========
One place where every piece of configuration is read.

Previously each script re-read os.environ with its own defaults, which meant
the loader and the MCP server could silently disagree about which cluster or
which password to use. Everything now goes through `settings`.

Values come from environment variables, with a .env file (if present in the
project root) loaded first as a convenience.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env reader. Existing environment variables always win."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


# The subset of the OpenSearch MCP server's ~40 tools that this POC enables.
# Handing an LLM all 40 tools wastes context and makes it pick cluster-admin
# tools for data questions. These five cover everything the demo needs.
DEFAULT_ENABLED_TOOLS = [
    "ListIndexTool",
    "IndexMappingTool",
    "SearchIndexTool",
    "GetIndexStatsTool",
    "MsearchTool",
]


@dataclass
class Settings:
    # --- OpenSearch -------------------------------------------------------
    opensearch_url: str = field(
        default_factory=lambda: os.environ.get("OPENSEARCH_URL", "https://localhost:9200")
    )
    opensearch_username: str = field(
        default_factory=lambda: os.environ.get("OPENSEARCH_USERNAME", "admin")
    )
    opensearch_password: str = field(
        default_factory=lambda: os.environ.get("OPENSEARCH_PASSWORD", "StrongPass@2026")
    )
    opensearch_verify_certs: bool = field(
        default_factory=lambda: _as_bool(os.environ.get("OPENSEARCH_VERIFY_CERTS", "false"))
    )

    # --- MCP server -------------------------------------------------------
    # "official"  -> uvx opensearch-mcp-server-py   (the real upstream server)
    # "local"     -> poc/local_mcp_server.py        (bundled, no uvx needed)
    mcp_server_mode: str = field(
        default_factory=lambda: os.environ.get("MCP_SERVER_MODE", "official")
    )
    enabled_tools: list[str] = field(
        default_factory=lambda: [
            t.strip()
            for t in os.environ.get("OPENSEARCH_ENABLED_TOOLS", ",".join(DEFAULT_ENABLED_TOOLS)).split(",")
            if t.strip()
        ]
    )

    # --- LLM --------------------------------------------------------------
    # anthropic | openai | groq | xai | rules
    llm_provider: str = field(
        default_factory=lambda: os.environ.get("LLM_PROVIDER", "rules").lower()
    )
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", ""))

    # --- Demo mode --------------------------------------------------------
    # When true, an in-process search engine stands in for a real cluster so
    # the UI can be demonstrated on a laptop with no Docker installed.
    demo_mode: bool = field(
        default_factory=lambda: _as_bool(os.environ.get("DEMO_MODE", "false"))
    )

    @property
    def api_key(self) -> str:
        return {
            "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
            "openai": os.environ.get("OPENAI_API_KEY", ""),
            "groq": os.environ.get("GROQ_API_KEY", ""),
            "xai": os.environ.get("XAI_API_KEY", ""),
            "rules": "",
        }.get(self.llm_provider, "")

    def mcp_env(self) -> dict[str, str]:
        """Environment handed to the MCP server subprocess."""
        env = dict(os.environ)
        env.update(
            {
                "OPENSEARCH_URL": self.opensearch_url,
                "OPENSEARCH_USERNAME": self.opensearch_username,
                "OPENSEARCH_PASSWORD": self.opensearch_password,
                "OPENSEARCH_SSL_VERIFY": "true" if self.opensearch_verify_certs else "false",
                "OPENSEARCH_ENABLED_TOOLS": ",".join(self.enabled_tools),
                # This POC only reads. Blocking writes at the server means a
                # confused LLM cannot delete an index.
                "OPENSEARCH_SETTINGS_ALLOW_WRITE": "false",
            }
        )
        return env

    def describe(self) -> dict:
        """Safe-to-display summary, used by the UI status bar."""
        return {
            "opensearch_url": self.opensearch_url,
            "mcp_server_mode": self.mcp_server_mode,
            "enabled_tools": self.enabled_tools,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model or "(provider default)",
            "api_key_present": bool(self.api_key),
            "demo_mode": self.demo_mode,
        }


settings = Settings()
