from apgkit.guard import SecurityGuard
from apgkit.metrics import Metrics
from fastapi import FastAPI, Response

app = FastAPI()
metrics = Metrics("demo", "1.0")
app.add_middleware(SecurityGuard, csrf_header="x-demo-csrf", max_body=1024, observe=metrics.observe)


@app.post("/api/notes")
def add(body: dict):
    return {"saved": True}


@app.get("/metrics")
def prom():
    body, ctype = metrics.render()
    return Response(body, media_type=ctype)
