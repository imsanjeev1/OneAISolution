from typing import Literal

from pydantic import BaseModel, Field


class TaskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)


class TaskResponse(BaseModel):
    feature: str
    result: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    collection_id: str = Field(default="", max_length=100)
    source: Literal["rag", "wiki", "confluence", "jira"] = "rag"
    question: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]


class ChatbotCollectionSummary(BaseModel):
    collection_id: str
    source: str
    chunks_indexed: int