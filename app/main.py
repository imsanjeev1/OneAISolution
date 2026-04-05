from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.schemas import ChatRequest, ChatResponse, ChatbotCollectionSummary, TaskRequest, TaskResponse
from app.services.databricks_service import FEATURE_PROMPTS, DatabricksService
from app.services.knowledge_source_service import KnowledgeSourceService
from app.services.rag_service import RagService
from app.settings import get_settings


settings = get_settings()
databricks_service = DatabricksService(settings)
rag_service = RagService(settings, databricks_service)
knowledge_source_service = KnowledgeSourceService(settings)

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "features": list(FEATURE_PROMPTS.keys()) + ["TextToimageAI", "ChatbotAI"],
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.post("/api/tasks/{feature}", response_model=TaskResponse)
async def run_task(feature: str, payload: TaskRequest) -> TaskResponse:
    result = databricks_service.run_feature(feature, payload.text)
    return TaskResponse(feature=feature, result=result)


@app.post("/api/text-to-image", response_model=TaskResponse)
async def text_to_image(
    prompt: str = Form(...),
    image: UploadFile = File(...),
) -> TaskResponse:
    image_bytes = await image.read()
    result = databricks_service.describe_image(prompt=prompt, image_bytes=image_bytes, filename=image.filename or "image.png")
    return TaskResponse(feature="TextToimageAI", result=result)


@app.post("/api/chatbot/upload")
async def upload_chatbot_document(file: UploadFile = File(...)) -> dict[str, str | int]:
    return await rag_service.ingest_document(file)


@app.get("/api/chatbot/collections", response_model=list[ChatbotCollectionSummary])
async def list_chatbot_collections() -> list[ChatbotCollectionSummary]:
    return [ChatbotCollectionSummary(**collection) for collection in rag_service.list_collections()]


@app.post("/api/chatbot/chat", response_model=ChatResponse)
async def chat_with_document(payload: ChatRequest) -> ChatResponse:
    if payload.source == "rag":
        response = rag_service.chat(payload.collection_id, payload.question, payload.history)
        return ChatResponse(**response)

    context, sources = knowledge_source_service.search(payload.source, payload.question)
    answer = databricks_service.chat_with_source_context(
        source=payload.source,
        question=payload.question,
        context=context,
        history=[message.model_dump() for message in payload.history],
    )
    return ChatResponse(answer=answer, sources=sources)