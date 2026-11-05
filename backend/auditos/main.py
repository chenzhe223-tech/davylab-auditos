from fastapi import FastAPI
from auditos.api.routes import router

app = FastAPI(
    title="DAVYLAB AuditOS API",
    version="0.1.0",
    description="Local-first installation engineering settlement audit assistant.",
)
app.include_router(router, prefix="/api")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "davylab-auditos"}
