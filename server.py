from __future__ import annotations

import io
from pathlib import Path
from typing import List

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE           = Path(__file__).parent
STATIC         = BASE / "static"
OUTPUT_FILE    = BASE / "output.csv"
APPROVED_FILE  = BASE / "approved-records.csv"
REFERENCE_FILE = BASE / "reference-tags.json"

app = FastAPI()


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_output() -> pd.DataFrame | None:
    if not OUTPUT_FILE.exists():
        return None
    df = pd.read_csv(OUTPUT_FILE)
    if "row_number" not in df.columns:
        df.insert(0, "row_number", range(1, len(df) + 1))
    df["tag"] = df["tag"].fillna("").astype(str).str.strip().replace("nan", "")
    return df


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/data")
async def get_data():
    df = read_output()
    if df is None:
        return {"stage": 0}

    rows = df.to_dict("records")
    for r in rows:
        r["row_number"] = int(r["row_number"])

    tags = sorted({r["tag"] for r in rows if r["tag"]})
    return {"stage": 1, "rows": rows, "tags": tags}


# ── Pydantic models ───────────────────────────────────────────────────────────

class RowData(BaseModel):
    row_number: int
    id: str
    quote: str
    tag: str

class SaveReq(BaseModel):
    rows: List[RowData]


# ── Save actions ──────────────────────────────────────────────────────────────

@app.post("/api/save_output")
async def save_output(req: SaveReq):
    df = pd.DataFrame([r.model_dump() for r in req.rows])
    df.to_csv(OUTPUT_FILE, index=False)
    return {"ok": True}


@app.post("/api/save_approved")
async def save_approved(req: SaveReq):
    rows = [{"id": r.id, "quote": r.quote, "tag": r.tag} for r in req.rows]
    pd.DataFrame(rows).to_csv(APPROVED_FILE, index=False)
    return {"ok": True}


@app.delete("/api/clear")
async def clear():
    for f in [OUTPUT_FILE, APPROVED_FILE, REFERENCE_FILE]:
        if f.exists():
            f.unlink()
    return {"ok": True}


# ── Downloads ─────────────────────────────────────────────────────────────────

@app.get("/download/output.csv")
async def dl_output():
    if not OUTPUT_FILE.exists():
        raise HTTPException(404)
    return FileResponse(OUTPUT_FILE, filename="output.csv", media_type="text/csv")


@app.get("/download/approved-records.csv")
async def dl_approved():
    if not APPROVED_FILE.exists():
        raise HTTPException(404)
    return FileResponse(APPROVED_FILE, filename="approved-records.csv", media_type="text/csv")


STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
