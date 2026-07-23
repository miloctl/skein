from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config, db
from .routes import api, chat

app = FastAPI(title="Strands Team Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api.router)
app.include_router(chat.router)


@app.on_event("startup")
def startup() -> None:
    db.init_db()


@app.get("/health")
def health():
    return {"ok": True, "model": config.MODEL_ID}
