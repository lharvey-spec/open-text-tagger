import json
import random
import pandas as pd
import streamlit as st
from pathlib import Path
from collections import Counter

st.set_page_config(page_title="Feedback Tagger", layout="wide")

BASE = Path(__file__).parent
PRETAGS_FILE           = BASE / "pretags.json"
APPROVED_EXAMPLES_FILE = BASE / "approved_examples.json"
RUN_REQUESTED_FILE     = BASE / "run_requested"
OUTPUT_FILE            = BASE / "tagged_output.csv"


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else None


pretags           = load_json(PRETAGS_FILE)
approved_examples = load_json(APPROVED_EXAMPLES_FILE)
run_requested     = RUN_REQUESTED_FILE.exists()
output_exists     = OUTPUT_FILE.exists()


# ── Stage 0: waiting for Claude ───────────────────────────────────────────────
if pretags is None:
    st.title("Feedback Tagger")
    st.info(
        "Share your CSV or Google Sheets link in the Claude Code chat, "
        "along with the column to analyse. Claude will read the first 100 rows, "
        "suggest tags, and load everything here."
    )
    if st.button("Refresh"):
        st.rerun()


# ── Stage 1: tag list + reference table ──────────────────────────────────────
elif not run_requested and not output_exists:
    st.title("Review & edit tags")

    # ── One-time initialisation ───────────────────────────────────────────────
    if "example_df" not in st.session_state:
        if approved_examples is not None:
            # Resume from last save
            st.session_state.example_df = pd.DataFrame([
                {"ID": ex.get("id", ""), "Quote": ex["quote"], "Tags": ", ".join(ex["tags"])}
                for ex in approved_examples["examples"]
            ])
            st.session_state.tag_list  = approved_examples["tags"][:]
            st.session_state.prev_tags = approved_examples["tags"][:]
        else:
            # Fresh load from pretags
            rows = pretags["rows"]
            random.shuffle(rows)
            st.session_state.example_df = pd.DataFrame([
                {"ID": r.get("id", ""), "Quote": r["quote"], "Tags": r["tags"]}
                for r in rows
            ])
            all_row_tags: set[str] = set()
            for r in rows:
                for t in r["tags"].split(","):
                    all_row_tags.add(t.strip())
            initial_tags = pretags["tags"] + sorted(all_row_tags - set(pretags["tags"]))
            st.session_state.tag_list  = initial_tags
            st.session_state.prev_tags = initial_tags[:]

    df = st.session_state.example_df

    # ── Left column: tag list ─────────────────────────────────────────────────
    col_left, col_right = st.columns([1, 3])

    with col_left:
        st.markdown("**Tag list**")
        st.caption("Rename a tag here and every row in the table updates automatically.")

        tag_text = st.text_area(
            "tag_list",
            value="\n".join(st.session_state.tag_list),
            height=360,
            label_visibility="collapsed",
        )
        current_tags = [t.strip() for t in tag_text.splitlines() if t.strip()]

        # Frequency counter
        tag_counts: Counter = Counter()
        for cell in df["Tags"]:
            for t in str(cell).split(","):
                t = t.strip()
                if t and t.lower() != "nan":
                    tag_counts[t] += 1
        if tag_counts:
            st.markdown("**Frequency in sample**")
            freq_df = pd.DataFrame(tag_counts.most_common(), columns=["Tag", "n"])
            st.dataframe(freq_df, hide_index=True, use_container_width=True)

    # ── Right column: reference table ────────────────────────────────────────
    with col_right:
        if "filter_tags" not in st.session_state:
            st.session_state.filter_tags = []
        filter_tags = st.multiselect(
            "Filter by tag",
            options=st.session_state.tag_list,
            default=[t for t in st.session_state.filter_tags if t in st.session_state.tag_list],
            placeholder="Show all rows",
            label_visibility="collapsed",
            key="filter_tags_widget",
        )
        st.session_state.filter_tags = filter_tags
        display_df = df[df["Tags"].apply(
            lambda cell: any(f in [t.strip() for t in str(cell).split(",")] for f in filter_tags)
        )] if filter_tags else df

        n_shown = len(display_df)
        n_rows  = len(df)
        st.caption(
            f"**{n_shown}**{'/' + str(n_rows) if filter_tags else ''} rows · Edit tags inline · "
            "To remove a row from the AI reference: click its row number to select, then press **Delete**"
        )

        row_h = st.slider(
            "Row height", min_value=40, max_value=200, value=80, step=5,
            label_visibility="collapsed"
        )

        edited_display = st.data_editor(
            display_df,
            column_config={
                "ID":    st.column_config.TextColumn("#", disabled=True, width="small"),
                "Quote": st.column_config.TextColumn("Quote", disabled=True, width="large"),
                "Tags":  st.column_config.TextColumn("Tags (comma-separated)", width="medium"),
            },
            use_container_width=True,
            hide_index=False,
            num_rows="dynamic",
            row_height=row_h,
            key="example_table",
        )
        # Strip phantom empty rows that data_editor adds when Enter is pressed
        edited_display = edited_display[
            edited_display["Quote"].notna() &
            (edited_display["Quote"].astype(str).str.strip() != "") &
            (edited_display["Quote"].astype(str) != "nan")
        ]
        # Merge edits/deletions back — only write when something actually changed
        if filter_tags:
            st.session_state.example_df.loc[edited_display.index, "Tags"] = edited_display["Tags"].values
            deleted = set(display_df.index) - set(edited_display.index)
            if deleted:
                st.session_state.example_df = st.session_state.example_df.drop(index=list(deleted))
        else:
            prev_df = st.session_state.example_df
            tags_changed = (
                len(edited_display) != len(prev_df) or
                not edited_display["Tags"].reset_index(drop=True).equals(
                    prev_df["Tags"].reset_index(drop=True)
                )
            )
            if tags_changed:
                st.session_state.example_df = edited_display

        # Sync any new tags typed in the table to the left-pane tag list immediately
        all_table_tags: set[str] = set()
        for cell in st.session_state.example_df["Tags"]:
            for t in str(cell).split(","):
                t = t.strip()
                if t and t.lower() not in ("nan", "none"):
                    all_table_tags.add(t)
        new_from_table = sorted(all_table_tags - set(st.session_state.tag_list))
        if new_from_table:
            st.session_state.tag_list = st.session_state.tag_list + new_from_table
            # Don't touch prev_tags — it tracks the textarea content only

    # ── Cascade + sync: runs AFTER merge-back so no edit is ever lost ─────────
    prev = st.session_state.prev_tags
    removed = set(prev) - set(current_tags)
    added   = set(current_tags) - set(prev)
    if len(removed) == 1 and len(added) == 1:
        old_tag, new_tag = list(removed)[0], list(added)[0]
        def _rename(cell):
            parts = [t.strip() for t in str(cell).split(",")]
            return ", ".join(new_tag if p == old_tag else p for p in parts)
        renamed = st.session_state.example_df.copy()
        renamed["Tags"] = renamed["Tags"].apply(_rename)
        st.session_state.example_df = renamed
        st.session_state.tag_list  = current_tags
        st.session_state.prev_tags = current_tags
        st.rerun()
    elif removed and not added:
        def _delete_tags(cell):
            parts = [t.strip() for t in str(cell).split(",") if t.strip() not in removed]
            return ", ".join(parts) if parts else "Unclear"
        updated = st.session_state.example_df.copy()
        updated["Tags"] = updated["Tags"].apply(_delete_tags)
        st.session_state.example_df = updated
        st.session_state.tag_list  = current_tags
        st.session_state.prev_tags = current_tags
        st.rerun()

    textarea_set = set(current_tags)
    extra_from_table = [t for t in st.session_state.tag_list if t not in textarea_set]
    st.session_state.tag_list  = current_tags + extra_from_table
    st.session_state.prev_tags = current_tags

    st.divider()
    final_tags = [t.strip() for t in tag_text.splitlines() if t.strip()]

    def build_examples(df, tags):
        out = []
        for _, row in df.iterrows():
            tags_clean = [
                t.strip() for t in str(row["Tags"]).split(",")
                if t.strip() and t.strip().lower() not in ("nan", "none")
            ]
            if tags_clean:
                entry = {"id": str(row.get("ID", "")), "quote": row["Quote"], "tags": tags_clean}
                out.append(entry)
        return {"tags": tags, "examples": out}

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])

    with c1:
        ref_csv = st.session_state.example_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "↓ Reference table (CSV)",
            data=ref_csv,
            file_name="reference_tags.csv",
            mime="text/csv",
        )
    with c2:
        pretags_bytes = PRETAGS_FILE.read_bytes() if PRETAGS_FILE.exists() else b""
        st.download_button(
            "↓ pretags.json",
            data=pretags_bytes,
            file_name="pretags.json",
            mime="application/json",
            disabled=not pretags_bytes,
        )
    with c3:
        if st.button("Save reference", disabled=not final_tags):
            payload = build_examples(st.session_state.example_df, final_tags)
            APPROVED_EXAMPLES_FILE.write_text(json.dumps(payload, indent=2))
            st.success(f"Saved — {len(payload['examples'])} rows · {len(final_tags)} tags")
        if APPROVED_EXAMPLES_FILE.exists():
            st.download_button(
                "↓ approved_examples.json",
                data=APPROVED_EXAMPLES_FILE.read_bytes(),
                file_name="approved_examples.json",
                mime="application/json",
            )
    with c4:
        if st.button("Run full analysis →", type="primary", disabled=not final_tags):
            payload = build_examples(st.session_state.example_df, final_tags)
            APPROVED_EXAMPLES_FILE.write_text(json.dumps(payload, indent=2))
            RUN_REQUESTED_FILE.touch()
            st.success(
                f"Reference saved ({len(payload['examples'])} rows · {len(final_tags)} tags). "
                "Tell Claude in chat to run the full analysis."
            )
            st.rerun()


# ── Stage 2: waiting for full analysis ───────────────────────────────────────
elif run_requested and not output_exists:
    approved = load_json(APPROVED_EXAMPLES_FILE)
    n_ex = len(approved.get("examples", []))
    st.title("Review & edit tags")
    st.success(f"Reference saved — {n_ex} rows · {len(approved.get('tags', []))} tags.")
    st.info("Claude is running the full analysis. Refresh when done.")
    if st.button("Refresh"):
        st.rerun()


# ── Stage 3: full results with editing ───────────────────────────────────────
else:
    # ── One-time initialisation ───────────────────────────────────────────────
    if "results_edit_df" not in st.session_state:
        full_df   = pd.read_csv(OUTPUT_FILE)
        quote_col = (pretags or {}).get("column")
        id_col    = (pretags or {}).get("id_column", "#")

        rows = []
        for _, r in full_df.iterrows():
            rows.append({
                "ID":    str(r[id_col])    if id_col    and id_col    in full_df.columns else "",
                "Quote": str(r[quote_col]) if quote_col and quote_col in full_df.columns else "",
                "Tags":  str(r["Tags"])    if "Tags"    in full_df.columns               else "",
            })
        st.session_state.results_full_df  = full_df
        st.session_state.results_edit_df  = pd.DataFrame(rows)
        st.session_state.results_show_sample = False

        base_tags = (approved_examples or {}).get("tags", [])
        all_output_tags: set[str] = set()
        for cell in full_df.get("Tags", pd.Series(dtype=str)):
            for t in str(cell).split(","):
                t = t.strip()
                if t and t.lower() != "nan":
                    all_output_tags.add(t)
        extra    = sorted(all_output_tags - set(base_tags))
        tag_list = base_tags + extra
        st.session_state.results_tag_list    = tag_list
        st.session_state.results_prev_tags   = tag_list[:]
        st.session_state.results_editor_ver  = 0

    edit_df     = st.session_state.results_edit_df
    show_sample = st.session_state.get("results_show_sample", False)
    n_total     = len(edit_df)

    sample_ids: set[str] = set()
    if approved_examples:
        sample_ids = {str(ex.get("id", "")) for ex in approved_examples.get("examples", [])}

    display_df = edit_df[edit_df["ID"].isin(sample_ids)] if show_sample else edit_df

    title_suffix = f" — sample view ({len(display_df)} rows)" if show_sample else f" — {n_total} rows"
    st.title("Results" + title_suffix)

    # ── Left column: tag list ─────────────────────────────────────────────────
    col_left, col_right = st.columns([1, 3])

    with col_left:
        st.markdown("**Tag list**")
        st.caption("Rename a tag here and every row updates automatically.")

        tag_text = st.text_area(
            "results_tag_list",
            value="\n".join(st.session_state.results_tag_list),
            height=360,
            label_visibility="collapsed",
        )
        current_tags = [t.strip() for t in tag_text.splitlines() if t.strip()]

        # Frequency counter
        tag_counts: Counter = Counter()
        for cell in edit_df["Tags"]:
            for t in str(cell).split(","):
                t = t.strip()
                if t and t.lower() != "nan":
                    tag_counts[t] += 1
        if tag_counts:
            st.markdown("**Frequency**")
            freq_df = pd.DataFrame(tag_counts.most_common(), columns=["Tag", "n"])
            st.dataframe(freq_df, hide_index=True, use_container_width=True)

    # ── Cascade + sync ────────────────────────────────────────────────────────
    r_prev    = st.session_state.results_prev_tags
    r_removed = set(r_prev) - set(current_tags)
    r_added   = set(current_tags) - set(r_prev)

    def _save_cascade():
        edit_df_now = st.session_state.results_edit_df
        full_df_upd = st.session_state.results_full_df.copy()
        full_df_upd.loc[edit_df_now.index, "Tags"] = edit_df_now["Tags"].values
        full_df_upd = full_df_upd.loc[edit_df_now.index]
        st.session_state.results_full_df = full_df_upd
        full_df_upd.to_csv(OUTPUT_FILE, index=False)

    if len(r_removed) == 1 and len(r_added) == 1:
        r_old, r_new = list(r_removed)[0], list(r_added)[0]
        def _rename_r(cell):
            parts = [t.strip() for t in str(cell).split(",")]
            return ", ".join(r_new if p == r_old else p for p in parts)
        renamed_r = st.session_state.results_edit_df.copy()
        renamed_r["Tags"] = renamed_r["Tags"].apply(_rename_r)
        st.session_state.results_edit_df   = renamed_r
        st.session_state.results_tag_list  = current_tags
        st.session_state.results_prev_tags = current_tags
        st.session_state.results_editor_ver = st.session_state.get("results_editor_ver", 0) + 1
        _save_cascade()
        st.toast(f"Renamed '{r_old}' → '{r_new}' across all rows and saved.")
        st.rerun()
    elif r_removed and not r_added:
        def _delete_r(cell):
            parts = [t.strip() for t in str(cell).split(",") if t.strip() not in r_removed]
            return ", ".join(parts) if parts else "Unclear"
        updated_r = st.session_state.results_edit_df.copy()
        updated_r["Tags"] = updated_r["Tags"].apply(_delete_r)
        st.session_state.results_edit_df   = updated_r
        st.session_state.results_tag_list  = current_tags
        st.session_state.results_prev_tags = current_tags
        st.session_state.results_editor_ver = st.session_state.get("results_editor_ver", 0) + 1
        _save_cascade()
        st.toast(f"Removed {r_removed} from all rows and saved.")
        st.rerun()
    else:
        r_textarea_set     = set(current_tags)
        r_extra_from_table = [t for t in st.session_state.results_tag_list if t not in r_textarea_set]
        st.session_state.results_tag_list  = current_tags + r_extra_from_table
        st.session_state.results_prev_tags = current_tags

    edit_df    = st.session_state.results_edit_df
    display_df = edit_df[edit_df["ID"].isin(sample_ids)] if show_sample else edit_df

    # ── Right column: table ───────────────────────────────────────────────────
    with col_right:
        filter_tags_r = st.multiselect(
            "Filter by tag",
            options=st.session_state.results_tag_list,
            placeholder="Show all rows",
            label_visibility="collapsed",
            key="results_filter",
        )
        if filter_tags_r:
            display_df = display_df[display_df["Tags"].apply(
                lambda cell: any(f in [t.strip() for t in str(cell).split(",")] for f in filter_tags_r)
            )]

        n_shown = len(display_df)
        label_suffix = "sample view" if show_sample else "rows"
        filter_suffix = f", {n_shown} matching filter" if filter_tags_r else ""
        st.caption(
            f"**{n_total}** {label_suffix}{filter_suffix} · "
            "Edit tags inline · Click row number then Delete to remove a row"
        )

        row_h = st.slider(
            "Row height", min_value=40, max_value=200, value=80, step=5,
            label_visibility="collapsed",
            key="results_row_h",
        )

        edited_display = st.data_editor(
            display_df,
            column_config={
                "ID":    st.column_config.TextColumn("#",                       disabled=True, width="small"),
                "Quote": st.column_config.TextColumn("Quote",                  disabled=True, width="large"),
                "Tags":  st.column_config.TextColumn("Tags (comma-separated)",               width="medium"),
            },
            use_container_width=True,
            hide_index=False,
            num_rows="dynamic",
            row_height=row_h,
            key=f"results_editor_{st.session_state.get('results_editor_ver', 0)}",
        )

        # Merge edits / deletions back into full edit_df
        if show_sample or filter_tags_r:
            st.session_state.results_edit_df.loc[edited_display.index, "Tags"] = edited_display["Tags"].values
            deleted_idx = set(display_df.index) - set(edited_display.index)
            if deleted_idx:
                st.session_state.results_edit_df = st.session_state.results_edit_df.drop(index=list(deleted_idx))
        else:
            st.session_state.results_edit_df = edited_display

    # ── Bottom bar ────────────────────────────────────────────────────────────
    st.divider()
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])

    with c1:
        label = "View all results" if show_sample else "View sample only"
        if st.button(label):
            st.session_state.results_show_sample = not show_sample
            st.rerun()

    with c2:
        if st.button("Save tag edits"):
            full_df_upd  = st.session_state.results_full_df.copy()
            edit_df_now  = st.session_state.results_edit_df
            full_df_upd.loc[edit_df_now.index, "Tags"] = edit_df_now["Tags"].values
            full_df_upd  = full_df_upd.loc[edit_df_now.index]
            st.session_state.results_full_df = full_df_upd
            full_df_upd.to_csv(OUTPUT_FILE, index=False)
            st.success("Saved.")

    with c3:
        full_df_dl  = st.session_state.results_full_df.copy()
        edit_df_dl  = st.session_state.results_edit_df
        full_df_dl.loc[edit_df_dl.index, "Tags"] = edit_df_dl["Tags"].values
        full_df_dl  = full_df_dl.loc[edit_df_dl.index]
        csv_bytes   = full_df_dl.to_csv(index=False).encode("utf-8")
        st.download_button(
            "↓ Download results (CSV)",
            data=csv_bytes,
            file_name="tagged_feedback.csv",
            mime="text/csv",
        )

    with c4:
        if st.button("Clear & start fresh", type="secondary"):
            for f in [PRETAGS_FILE, APPROVED_EXAMPLES_FILE, RUN_REQUESTED_FILE, OUTPUT_FILE]:
                if f.exists():
                    f.unlink()
            for key in ["example_df", "tag_list", "prev_tags",
                        "results_edit_df", "results_full_df", "results_tag_list",
                        "results_prev_tags", "results_show_sample"]:
                st.session_state.pop(key, None)
            st.rerun()
