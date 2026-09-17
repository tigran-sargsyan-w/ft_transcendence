from fastapi import FastAPI

app = FastAPI(title="Graph Engine", version="0.1.0")


@app.get("/api/v1/health")
def health() -> dict:
    return {"data": {"status": "ok"}}
