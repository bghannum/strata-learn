from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import repos
from app.config import settings

app = FastAPI(title="Strata Learn API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(repos.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
