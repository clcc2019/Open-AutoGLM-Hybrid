"""Reply Agent Service configuration.

Priority: environment variables > .env file > defaults.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM ---
    llm_provider: str = Field(default="openai_compatible", description="openai_compatible | deepseek | openai")
    llm_model: str = Field(default="deepseek-chat", description="Model ID")
    llm_api_key: str = Field(default="", description="API key for the LLM provider")
    llm_base_url: str = Field(default="https://api.deepseek.com/v1", description="OpenAI-compatible base URL")

    # --- Embedding (for RAG knowledge base) ---
    embedding_model: str = Field(default="text-embedding-3-small", description="Embedding model ID")
    embedding_api_key: str = Field(default="", description="API key for embeddings (defaults to llm_api_key)")
    embedding_base_url: str = Field(default="https://api.openai.com/v1", description="Embedding API base URL")
    embedding_dimensions: int = Field(default=1536, description="Embedding vector dimensions")

    # --- Database ---
    database_url: str = Field(default="sqlite:///reply_agent.db", description="postgresql://... or sqlite:///...")

    # --- Knowledge ---
    knowledge_docs_dir: str = Field(default="knowledge_docs", description="Path to knowledge documents directory")
    lancedb_uri: str = Field(default="tmp/lancedb", description="LanceDB storage URI")

    # --- Agent ---
    agent_host: str = Field(default="0.0.0.0")
    agent_port: int = Field(default=8080)

    # --- Vision LLM (for screenshot analysis) ---
    vision_model: str = Field(default="", description="Vision model ID (defaults to llm_model)")
    vision_api_key: str = Field(default="", description="Vision model API key (defaults to llm_api_key)")
    vision_base_url: str = Field(default="", description="Vision model base URL (defaults to llm_base_url)")

    # --- Security ---
    api_key: str = Field(default="", description="API key for authentication (empty = no auth)")

    # --- Business ---
    min_price_ratio: float = Field(default=0.8, description="Minimum acceptable price ratio for bargaining")
    auto_escalate_keywords: str = Field(
        default="投诉,举报,消协,工商,律师",
        description="Comma-separated keywords that trigger human escalation",
    )

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def escalate_keywords_list(self) -> list[str]:
        return [k.strip() for k in self.auto_escalate_keywords.split(",") if k.strip()]

    @property
    def effective_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.llm_api_key

    @property
    def effective_vision_model(self) -> str:
        return self.vision_model or self.llm_model

    @property
    def effective_vision_api_key(self) -> str:
        return self.vision_api_key or self.llm_api_key

    @property
    def effective_vision_base_url(self) -> str:
        return self.vision_base_url or self.llm_base_url


settings = Settings()
