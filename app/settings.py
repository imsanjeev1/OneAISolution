from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    app_name: str = "OneAI Solution"
    app_env: str = "development"
    databricks_host: str = Field(default="", alias="DATABRICKS_HOST")
    databricks_token: str = Field(default="", alias="DATABRICKS_TOKEN")
    databricks_chat_model: str = Field(default="databricks-meta-llama-3-3-70b-instruct", alias="DATABRICKS_CHAT_MODEL")
    databricks_vision_model: str = Field(default="databricks-llama-4-maverick", alias="DATABRICKS_VISION_MODEL")
    databricks_embedding_model: str = Field(default="databricks-gte-large-en", alias="DATABRICKS_EMBEDDING_MODEL")
    databricks_image_description_prompt: str = Field(
        default="Describe the image accurately and answer the user's prompt. Return plain text only.",
        alias="DATABRICKS_IMAGE_DESCRIPTION_PROMPT",
    )
    huggingface_embedding_model: str = Field(
        default="sentence-transformers/all-mpnet-base-v2",
        alias="HUGGINGFACE_EMBEDDING_MODEL",
    )
    chroma_dir: str = Field(default=str(BASE_DIR / "data" / "chroma"), alias="CHROMA_DIR")
    uploads_dir: str = Field(default=str(BASE_DIR / "data" / "uploads"), alias="UPLOADS_DIR")
    max_upload_bytes: int = Field(default=15 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")
    wiki_api_url: str = Field(default="https://en.wikipedia.org/w/api.php", alias="WIKI_API_URL")
    wiki_token: str = Field(default="", alias="WIKI_TOKEN")
    wiki_search_limit: int = Field(default=3, alias="WIKI_SEARCH_LIMIT")
    confluence_base_url: str = Field(default="", alias="CONFLUENCE_BASE_URL")
    confluence_email: str = Field(default="", alias="CONFLUENCE_EMAIL")
    confluence_api_token: str = Field(default="", alias="CONFLUENCE_API_TOKEN")
    confluence_space_key: str = Field(default="", alias="CONFLUENCE_SPACE_KEY")
    confluence_search_limit: int = Field(default=3, alias="CONFLUENCE_SEARCH_LIMIT")
    jira_base_url: str = Field(default="", alias="JIRA_BASE_URL")
    jira_email: str = Field(default="", alias="JIRA_EMAIL")
    jira_api_token: str = Field(default="", alias="JIRA_API_TOKEN")
    jira_project_key: str = Field(default="", alias="JIRA_PROJECT_KEY")
    jira_search_limit: int = Field(default=5, alias="JIRA_SEARCH_LIMIT")

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    Path(settings.chroma_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.uploads_dir).mkdir(parents=True, exist_ok=True)
    return settings