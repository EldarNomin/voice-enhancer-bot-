from fastapi import FastAPI

app = FastAPI(title="Voice Enhancer Bot API", docs_url=None, redoc_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
