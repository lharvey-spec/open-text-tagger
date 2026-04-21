from __future__ import annotations

import io
import json
from collections import Counter
from pathlib import Path
from typing import List

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE                   = Path(__file__).parent
STATIC                 = BASE / "static"
PRETAGS_FILE           = BASE / "pretags.json"
APPROVED_EXAMPLES_FILE = BASE / "approved_examples.json"
RUN_REQUESTED_FILE     = BASE / "run_requested"
OUTPUT_FILE            = BASE / "tagged_output.csv"

app = FastAPI()


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def get_stage() -> int:
    if not PRETAGS_FILE.exists():   return 0
    if OUTPUT_FILE.exists():        return 3
    if RUN_REQUESTED_FILE.exists(): return 2
    return 1


def build_payload(rows: list[dict], tags: list[str], pretags: dict) -> dict:
    return {
        "column":    pretags.get("column"),
        "id_column": pretags.get("id_column", "#"),
        "tags":      tags,
        "examples": [
            {
                "id":    r["id"],
                "quote": r["quote"],
                "tags":  [t.strip() for t in r["tags"].split(",") if t.strip()],
            }
            for r in rows
        ],
    }


def write_tags_to_csv(rows: list[dict]):
    if not OUTPUT_FILE.exists():
        return
    full_df = pd.read_csv(OUTPUT_FILE)
    pretags = load_json(PRETAGS_FILE) or {}
    id_col  = pretags.get("id_column", "#")
    if id_col in full_df.columns:
        tag_map = {r["id"]: r["tags"] for r in rows}
        if "Tags" not in full_df.columns:
            full_df["Tags"] = ""
        full_df["Tags"] = full_df[id_col].astype(str).map(tag_map).fillna(full_df["Tags"])
        full_df.to_csv(OUTPUT_FILE, index=False)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/data")
async def get_data():
    stage = get_stage()
    out: dict = {"stage": stage}

    if stage == 1:
        pretags  = load_json(PRETAGS_FILE) or {}
        approved = load_json(APPROVED_EXAMPLES_FILE)
        if approved:
            tags = approved.get("tags", [])
            rows = [
                {"id": str(ex.get("id", "")), "quote": ex["quote"],
                 "tags": ", ".join(ex.get("tags", []))}
                for ex in approved.get("examples", [])
            ]
        else:
            tags = pretags.get("tags", [])
            rows = [
                {"id": str(r.get("id", "")), "quote": r.get("quote", ""),
                 "tags": ", ".join(r.get("tags", []))}
                for r in pretags.get("rows", [])
            ]
        out.update(tags=tags, rows=rows)

    elif stage == 3:
        pretags   = load_json(PRETAGS_FILE) or {}
        approved  = load_json(APPROVED_EXAMPLES_FILE)
        full_df   = pd.read_csv(OUTPUT_FILE)
        quote_col = pretags.get("column")
        id_col    = pretags.get("id_column", "#")

        rows = []
        for i, r in full_df.iterrows():
            rows.append({
                "id":    str(r[id_col])    if id_col    and id_col    in full_df.columns else str(i),
                "quote": str(r[quote_col]) if quote_col and quote_col in full_df.columns else "",
                "tags":  str(r["Tags"])    if "Tags"    in full_df.columns               else "",
            })

        base_tags: list[str] = (approved or {}).get("tags", [])
        all_tags: set[str]   = set()
        for r in rows:
            for t in r["tags"].split(","):
                t = t.strip()
                if t and t.lower() != "nan":
                    all_tags.add(t)
        extra = sorted(all_tags - set(base_tags))
        tags  = base_tags + extra

        sample_ids: list[str] = []
        if approved:
            sample_ids = [str(ex.get("id", "")) for ex in approved.get("examples", [])]

        out.update(tags=tags, rows=rows, sample_ids=sample_ids)

    return out


# ── Pydantic models ───────────────────────────────────────────────────────────

class RowData(BaseModel):
    id: str
    quote: str
    tags: str

class SaveRefReq(BaseModel):
    tags: List[str]
    rows: List[RowData]

class SaveTagsReq(BaseModel):
    rows: List[RowData]


# ── Stage 1 actions ───────────────────────────────────────────────────────────

@app.post("/api/save_reference")
async def save_reference(req: SaveRefReq):
    pretags = load_json(PRETAGS_FILE) or {}
    payload = build_payload([r.model_dump() for r in req.rows], req.tags, pretags)
    APPROVED_EXAMPLES_FILE.write_text(json.dumps(payload, indent=2))
    return {"ok": True}


@app.post("/api/run_analysis")
async def run_analysis(req: SaveRefReq):
    await save_reference(req)
    RUN_REQUESTED_FILE.touch()
    return {"ok": True}


# ── Stage 3 actions ───────────────────────────────────────────────────────────

@app.post("/api/save_tags")
async def save_tags(req: SaveTagsReq):
    if not OUTPUT_FILE.exists():
        raise HTTPException(404, "No output file found")
    write_tags_to_csv([r.model_dump() for r in req.rows])
    return {"ok": True}


@app.delete("/api/clear")
async def clear():
    for f in [PRETAGS_FILE, APPROVED_EXAMPLES_FILE, RUN_REQUESTED_FILE, OUTPUT_FILE]:
        if f.exists():
            f.unlink()
    return {"ok": True}


# ── Downloads ─────────────────────────────────────────────────────────────────

@app.get("/download/pretags.json")
async def dl_pretags():
    if not PRETAGS_FILE.exists(): raise HTTPException(404)
    return FileResponse(PRETAGS_FILE, filename="pretags.json", media_type="application/json")

@app.get("/download/approved_examples.json")
async def dl_approved():
    if not APPROVED_EXAMPLES_FILE.exists(): raise HTTPException(404)
    return FileResponse(APPROVED_EXAMPLES_FILE, filename="approved_examples.json",
                        media_type="application/json")

@app.get("/download/tagged_output.csv")
async def dl_output():
    if not OUTPUT_FILE.exists(): raise HTTPException(404)
    return FileResponse(OUTPUT_FILE, filename="tagged_output.csv", media_type="text/csv")

@app.get("/download/reference_tags.csv")
async def dl_reference():
    approved = load_json(APPROVED_EXAMPLES_FILE)
    if not approved: raise HTTPException(404)
    rows = [
        {"ID": ex.get("id", ""), "Quote": ex["quote"], "Tags": ", ".join(ex.get("tags", []))}
        for ex in approved.get("examples", [])
    ]
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reference_tags.csv"},
    )


STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
