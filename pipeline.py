"""
Analysis pipeline helpers — called by Claude in the chat during steps 2-9.

Usage example:
    from pipeline import *
    prepare_output("/path/to/data.csv", quote_col="What should be improved", id_col="Response ID", n_rows=200)
    print_unique_tags()
    apply_tag_changes(renames={"dark mode": "UI - dark mode"}, removals=["Other"])
    build_reference_tags(instructions=["Tags should follow 'Category - specific issue' format"])
    n = apply_pretags()
    print(f"{n} rows pre-tagged from approved-records.csv")
    ref = read_reference_tags()   # MUST call before tagging
    untagged = get_untagged_rows()
    # Claude consults ref, assigns tags reusing existing ones where possible, then:
    save_tags([{"id": "abc", "tag": "UI - dark mode"}, ...])
    print_tag_summary()
"""

import json
from collections import Counter
from pathlib import Path

import pandas as pd

BASE          = Path(__file__).parent
OUTPUT_FILE   = BASE / "output.csv"
APPROVED_FILE = BASE / "approved-records.csv"
REFERENCE_FILE = BASE / "reference-tags.json"


# ── Step 2 ────────────────────────────────────────────────────────────────────

def prepare_output(source_path: str, quote_col: str, id_col: str, n_rows: int) -> pd.DataFrame:
    """Read source CSV, filter blank quotes, take first n_rows, write output.csv."""
    df = pd.read_csv(source_path)

    # Filter blank quotes
    df = df[df[quote_col].notna() & (df[quote_col].astype(str).str.strip() != "")]
    df = df.head(n_rows).reset_index(drop=True)

    out = pd.DataFrame({
        "row_number": range(1, len(df) + 1),
        "id":         df[id_col].astype(str).values,
        "quote":      df[quote_col].astype(str).values,
        "tag":        "",
    })
    out.to_csv(OUTPUT_FILE, index=False)
    print(f"output.csv written: {len(out)} rows")
    return out


# ── Step 3 ────────────────────────────────────────────────────────────────────

def print_unique_tags() -> list[str]:
    """Print unique tags from approved-records.csv."""
    if not APPROVED_FILE.exists():
        print("No approved-records.csv found — no existing tags.")
        return []
    df = pd.read_csv(APPROVED_FILE)
    tags = sorted(df["tag"].dropna().astype(str).str.strip().unique().tolist())
    tags = [t for t in tags if t and t.lower() != "nan"]
    if tags:
        print(f"{len(tags)} existing tags:")
        for t in tags:
            print(f"  - {t}")
    else:
        print("approved-records.csv exists but contains no tags yet.")
    return tags


# ── Step 5 ────────────────────────────────────────────────────────────────────

def apply_tag_changes(renames: dict[str, str] | None = None,
                      removals: list[str] | None = None):
    """Apply renames and/or removals to both approved-records.csv and output.csv."""
    for filepath in [APPROVED_FILE, OUTPUT_FILE]:
        if not filepath.exists():
            continue
        df = pd.read_csv(filepath)
        if "tag" not in df.columns:
            continue
        if renames:
            for old, new in renames.items():
                df["tag"] = df["tag"].astype(str).replace(old, new)
        if removals:
            rset = set(removals)
            df["tag"] = df["tag"].apply(lambda t: "" if str(t).strip() in rset else t)
        df.to_csv(filepath, index=False)
    print("Tag changes applied.")


# ── Step 6 ────────────────────────────────────────────────────────────────────

def build_reference_tags(instructions: list[str] | None = None) -> dict:
    """Rebuild tags/examples from approved-records.csv; preserve existing instructions.

    Existing instructions in reference-tags.json are kept unless new ones are passed,
    in which case the new list replaces them. Tags and examples are always rebuilt fresh.
    Instructions are only wiped by DELETE /api/clear (which deletes the file entirely).
    """
    # Preserve existing instructions unless caller explicitly provides new ones
    existing_instructions: list[str] = []
    if REFERENCE_FILE.exists():
        try:
            existing_instructions = json.loads(REFERENCE_FILE.read_text()).get("instructions", [])
        except Exception:
            pass

    tag_examples: dict[str, list[str]] = {}
    if APPROVED_FILE.exists():
        df = pd.read_csv(APPROVED_FILE)
        df = df[df["tag"].notna() & (df["tag"].astype(str).str.strip() != "")]
        df = df[df["tag"].astype(str).str.lower() != "nan"]
        for tag, group in df.groupby("tag"):
            tag_examples[str(tag)] = group["quote"].dropna().astype(str).head(3).tolist()

    payload = {
        "instructions": instructions if instructions is not None else existing_instructions,
        "tags": [
            {"tag": tag, "examples": examples}
            for tag, examples in sorted(tag_examples.items())
        ],
    }
    REFERENCE_FILE.write_text(json.dumps(payload, indent=2))
    print(f"reference-tags.json written: {len(payload['tags'])} tags, "
          f"{len(payload['instructions'])} instruction(s)")
    return payload


# ── Step 7 ────────────────────────────────────────────────────────────────────

def apply_pretags() -> int:
    """Copy tags from approved-records.csv into output.csv where IDs match."""
    if not APPROVED_FILE.exists() or not OUTPUT_FILE.exists():
        print("Nothing to pre-tag (missing files).")
        return 0

    approved_df = pd.read_csv(APPROVED_FILE)
    output_df   = pd.read_csv(OUTPUT_FILE)
    output_df["tag"] = output_df["tag"].astype(object)

    id_tag_map = {
        str(row["id"]): str(row["tag"])
        for _, row in approved_df.iterrows()
        if pd.notna(row.get("tag")) and str(row.get("tag", "")).strip().lower() not in ("", "nan")
    }

    count = 0
    for i, row in output_df.iterrows():
        current_tag = str(row.get("tag", "")).strip()
        if current_tag in ("", "nan"):
            matched = id_tag_map.get(str(row["id"]), "")
            if matched:
                output_df.at[i, "tag"] = matched
                count += 1

    output_df.to_csv(OUTPUT_FILE, index=False)
    print(f"{count} rows pre-tagged from approved-records.csv")
    return count


# ── Step 8 helpers ────────────────────────────────────────────────────────────

def read_reference_tags() -> dict:
    """Print and return reference-tags.json so Claude consults it before tagging."""
    if not REFERENCE_FILE.exists():
        print("No reference-tags.json found.")
        return {}
    payload = json.loads(REFERENCE_FILE.read_text())
    print(f"Instructions: {payload.get('instructions') or 'none'}\n")
    print(f"{len(payload.get('tags', []))} existing tags:")
    for t in payload.get("tags", []):
        print(f"  - {t['tag']}")
        for ex in t.get("examples", []):
            print(f"      e.g. {ex[:80]}")
    return payload


def get_untagged_rows() -> list[dict]:
    """Return rows from output.csv that still need a tag."""
    if not OUTPUT_FILE.exists():
        return []
    df = pd.read_csv(OUTPUT_FILE)
    mask = df["tag"].isna() | (df["tag"].astype(str).str.strip().isin(["", "nan"]))
    rows = df[mask][["row_number", "id", "quote"]].to_dict("records")
    print(f"{len(rows)} rows need tagging")
    return rows


def save_tags(tagged_rows: list[dict]):
    """Write AI-assigned tags back to output.csv. Each item needs 'id' and 'tag'."""
    if not OUTPUT_FILE.exists():
        print("output.csv not found.")
        return
    df      = pd.read_csv(OUTPUT_FILE)
    df["tag"] = df["tag"].astype(object)
    tag_map = {str(r["id"]): str(r["tag"]) for r in tagged_rows}
    for i, row in df.iterrows():
        if str(row["id"]) in tag_map:
            df.at[i, "tag"] = tag_map[str(row["id"])]
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"Tags saved for {len(tagged_rows)} rows.")


# ── Step 9 ────────────────────────────────────────────────────────────────────

def print_tag_summary():
    """Print unique tags and occurrence counts from output.csv."""
    if not OUTPUT_FILE.exists():
        print("No output.csv found.")
        return
    df     = pd.read_csv(OUTPUT_FILE)
    counts = Counter(
        t for t in df["tag"].dropna().astype(str).str.strip()
        if t and t.lower() != "nan"
    )
    total_tagged   = sum(counts.values())
    total_rows     = len(df)
    total_untagged = total_rows - total_tagged

    print(f"\nTag summary ({total_tagged}/{total_rows} tagged, {total_untagged} untagged):\n")
    for tag, n in counts.most_common():
        print(f"  {n:>4}  {tag}")
