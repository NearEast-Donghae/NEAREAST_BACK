from fastapi import FastAPI

app = FastAPI(title="NEAREAST")


@app.get("/health")
def health():
    return {"status": "ok"}
