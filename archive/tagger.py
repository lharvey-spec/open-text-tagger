import anthropic
import json
import re

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"


def pretag_samples(feedback_samples: list[str], approved_tags: list[str]) -> list[dict]:
    """Tag the sample rows (used before user approves examples)."""
    return _run_tag_batches(feedback_samples, approved_tags, examples=[])


def tag_all_feedback(feedback_list: list[str], approved_tags: list[str], examples: list[dict]) -> list[list[str]]:
    """Tag every row using approved tags + few-shot examples."""
    return _run_tag_batches(feedback_list, approved_tags, examples)


def _run_tag_batches(feedback_list, approved_tags, examples, batch_size=20):
    all_results = []
    new_tags_seen: set = set()
    total = len(feedback_list)

    for i in range(0, total, batch_size):
        batch = feedback_list[i: i + batch_size]
        batch_results = _tag_batch(batch, approved_tags, new_tags_seen, examples)
        all_results.extend(batch_results)
        print(f"  Tagged {min(i + batch_size, total)}/{total}")

    return all_results


def _tag_batch(batch: list[str], approved_tags: list[str], new_tags_seen: set, examples: list[dict]) -> list[list[str]]:
    all_tags = approved_tags + sorted(new_tags_seen)
    tags_list = "\n".join(f"- {t}" for t in all_tags)
    numbered  = "\n".join(f"{i+1}. {fb}" for i, fb in enumerate(batch))

    examples_section = ""
    if examples:
        lines = []
        for ex in examples[:15]:
            lines.append(f'  Feedback: "{ex["quote"]}"\n  Tags: {", ".join(ex["tags"])}')
        examples_section = "\n\nHere are approved examples to guide your tagging:\n" + "\n\n".join(lines)

    prompt = f"""You are tagging open-text product feedback from music software surveys (LANDR, Reason, Synchro Arts).

Approved tags:
{tags_list}{examples_section}

Rules:
- Assign one or more tags to each response (comma-separated).
- Only use tags from the approved list — UNLESS nothing fits. In that case invent ONE new tag at the same level of specificity (2–4 words, title case) and prefix it with "NEW: ".
- If a response is blank or gibberish, return "Unclear".

Feedback to tag:
{numbered}

Respond with a JSON array ONLY — one entry per item, same order:
["Tag A, Tag B", "Tag C", "NEW: My New Tag", ...]"""

    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    results = json.loads(raw)

    for tag_str in results:
        for tag in tag_str.split(","):
            tag = tag.strip()
            if tag.startswith("NEW: "):
                new_tags_seen.add(tag[5:].strip())

    return [
        [t.strip().removeprefix("NEW: ") for t in tag_str.split(",")]
        for tag_str in results
    ]
