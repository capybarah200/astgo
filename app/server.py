"""Веб-сервер: диалог (POST /api/chat) и страница с картой.

Запуск:  python -m app            (http://127.0.0.1:8000)
         python -m app --host 0.0.0.0   — открыть с телефона в той же сети
Для демо время можно задать в ссылке: http://127.0.0.1:8000/?at=23:00
"""

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import load_settings
from app.engine import Catalog, load_landmarks
from app.llm import Gemini
from app.places import load_areas
from app.service import Assistant, RequestError

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 32_768  # реплика ≤ 500 символов + контекст разговора


class ChatRequest(BaseModel):
    message: str = Field("", max_length=500)
    state: dict | None = None  # контекст разговора от клиента; сервер его проверяет (sanitize_state)
    lat: float | None = Field(None, ge=-90, le=90)  # точка на карте или геолокация, уже округлённая
    lon: float | None = Field(None, ge=-180, le=180)
    source: Literal["map", "geo"] | None = None
    at: str | None = Field(None, pattern=r"^\d{1,2}:\d{2}$")


def build_assistant() -> Assistant:
    settings = load_settings()
    gemini = (Gemini(settings.gemini_api_key, settings.gemini_model, thinking=settings.gemini_thinking)
              if settings.has_gemini else None)
    return Assistant(Catalog.load(), load_landmarks(), gemini, areas=load_areas())


def create_app(assistant: Assistant | None = None) -> FastAPI:
    assistant = assistant or build_assistant()
    app = FastAPI(title="AstGo — куда пойти в Астане", docs_url="/api/docs", redoc_url=None)

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        """Страница и скрипты сверяются с сервером при каждом открытии: после обновления не будет старой версии.

        Заодно ограничение тела запроса: реплика и контекст — это килобайты, а мегабайтный JSON
        не должен разбираться целиком до проверки.
        """
        size = request.headers.get("content-length")
        if size and (not size.isdigit() or int(size) > MAX_BODY_BYTES):
            return JSONResponse({"detail": "слишком большой запрос"}, status_code=413)
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/status")
    def status():
        return {"places": len(assistant.catalog.pois), "llm": assistant.gemini is not None,
                "model": ", ".join(assistant.gemini.models) if assistant.gemini else None}

    @app.post("/api/chat")
    def chat(req: ChatRequest):
        point = None
        if req.lat is not None and req.lon is not None:
            point = {"lat": req.lat, "lon": req.lon, "source": req.source or "map"}
        try:
            return assistant.chat(req.message, req.state, point, req.at)
        except RequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
