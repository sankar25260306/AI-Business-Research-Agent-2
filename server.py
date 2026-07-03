"""
server.py
FastAPI + WebSocket server. Streams every pipeline event (discovery,
extraction, verification, completion) to the browser UI in real time
(Phase 10), and exposes JSON/CSV export endpoints.

Run:
    python run_server.py
Then open http://127.0.0.1:8000
"""

import asyncio
import os
import sys

# Windows fix: the default asyncio event loop (Selector) on Windows does NOT
# support subprocesses, which Playwright needs to launch the browser.
# This must run before any asyncio loop is created.
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from core.pipeline import run_pipeline
from agents.reporting_agent import ReportingAgent

app = FastAPI(title="AI Business Research Agent", version="1.0")
allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
    if origin.strip()
]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["*"], allow_headers=["*"])

_reports: dict[str, dict] = {}


@app.get("/")
async def index():
    return FileResponse("frontend/index.html")


@app.websocket("/ws/research")
async def ws_research(ws: WebSocket):
    await ws.accept()
    try:
        raw = await ws.receive_text()
        payload = json.loads(raw)
        query = payload.get("query", "").strip()
        if not query:
            await ws.send_text(json.dumps({"event_type": "error", "message": "Empty query"}))
            return
        if len(query) > 200:
            await ws.send_text(json.dumps({"event_type": "error", "message": "Query is too long"}))
            return

        async def emit(event_type, message, progress, data=None):
            await ws.send_text(json.dumps({
                "event_type": event_type, "message": message,
                "progress": progress, "data": data,
            }))

        report = await run_pipeline(query, emit=emit)
        session_id = str(report.get("generated_at"))
        _reports[session_id] = report

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_text(json.dumps({"event_type": "error", "message": str(e)}))
        except Exception:
            pass


@app.get("/api/export/json/{session_id}")
async def export_json(session_id: str):
    return JSONResponse(_reports.get(session_id, {}))


@app.get("/api/export/csv/{session_id}")
async def export_csv(session_id: str):
    report = _reports.get(session_id, {})
    csv_text = ReportingAgent().to_csv(report.get("businesses", []))
    return PlainTextResponse(
        csv_text, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=businesses.csv"},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
