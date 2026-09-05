from __future__ import annotations

import json
from pathlib import Path

from tools.runner_types import StageExecutionContext, safe_q1_feedback


def _canonical_output_paths(stage: str, keyword: str | None) -> str:
    if stage == "topic-selector":
        if keyword is None:
            return (
                " Canonical output path rule: create exactly one file at "
                "research/topic-selection-<resolved keyword>.md, substituting the "
                "resolved keyword verbatim after the topic-selection- prefix."
            )
        return (
            " Canonical output path: "
            f"research/topic-selection-{keyword}.md."
        )
    if keyword is None:
        return ""
    if stage == "researcher":
        return f" Canonical output path: research/{keyword}.md."
    if stage == "writer":
        return f" Canonical output path: drafts/{keyword}.md."
    if stage == "image-maker":
        return f" Canonical output directory: assets/{keyword}/."
    if stage == "content-assembler":
        return (
            " Canonical output paths: "
            f"final/{keyword}.md, "
            f"final/{keyword}-naver-layout.md, "
            f"final/{keyword}-naver-copy.md, and "
            f"final/{keyword}-naver-input.md."
        )
    return ""


def stage_prompt(
    context: StageExecutionContext, instruction_path: Path, output_path: Path
) -> str:
    agents_path = context.root / "AGENTS.md"
    prompt = (
        f"Execute stage {context.stage} for run_id={context.run_id}, "
        f"topic_id={context.topic_id}, keyword={context.keyword or 'auto-topic'}. "
        f"Read {agents_path} and {instruction_path}; treat only those two files as "
        "project instructions and follow both. Mirror declared canonical relative artifact "
        "paths under the staging workspace. Do not write to the source project. "
        "Return those relative paths in the structured result. "
        f"Do not include the internal protocol file {output_path} in artifacts. "
        f"The trusted work directory {context.work_dir} is read-only; never write there. "
        "Do not call external write tools unless "
        "this stage permits it."
    )
    if context.stage not in {"notion-rider", "naver-rider"}:
        prompt += (
            " The structured result field run_status must be null for this producer "
            "stage; only notion-rider and naver-rider may set terminal run_status values."
        )
    canonical_paths = _canonical_output_paths(context.stage, context.keyword)
    if canonical_paths:
        literal_path_instruction = "Treat these paths as literal. Preserve every space and Unicode character in the keyword verbatim; do not slugify, normalize, transliterate, rename, or replace whitespace."
        prompt += canonical_paths + " " + literal_path_instruction
    if context.stage == "researcher":
        if context.keyword is not None:
            prompt += (
                " Read the canonical stage input from the source project at "
                f"{context.root / 'research' / f'topic-selection-{context.keyword}.md'}."
            )
        prompt += (
            " Do not spawn subagents. Execute the official and supporting-visual "
            "research lanes serially in this process, starting the next lane only "
            "after the previous lane completes. Read-only browser access is authorized "
            "for this stage through Aside; use it to open and inspect the original "
            "pages, including JavaScript-rendered pages and the user-supplied supporting "
            "URLs. Do not click, fill, submit, save, download, or otherwise write through "
            "the browser. Record the URL, access time, observed facts, and rights "
            "limitations in the research artifact. For every visual slot that the writer "
            "will express as an [IMAGE:] marker, choose an asset_type from the image-maker "
            "shared type list and require a real image file. Do not use text_only, "
            "comparison_table, checklist, or another non-shared type for an image marker; "
            "represent comparisons with side_by_side or chart and checklists with "
            "process_flow so marker metadata can remain identical through assembly."
        )
    if context.stage == "topic-selector":
        prompt += (
            " The only candidate source for automatic topic selection is the Naver Creator "
            "Advisor page named by the topic-selector instructions. Use read-only browser "
            "access through Aside, record the visible keyword exactly as shown, and save "
            "the raw observation in metadata/creator-advisor/<as_of_date>/<capture_id>.json. "
            "Do not invent keywords, estimate missing values, submit, save, download, or "
            "otherwise write through the browser. If the page cannot be inspected, stop "
            "with a concrete access limitation."
        )
    if context.stage == "writer" and context.keyword is not None:
        prompt += (
            " Read the canonical source-project inputs at "
            f"{context.root / 'research' / f'{context.keyword}.md'}, "
            f"{context.root / 'style-guide.md'}, and {context.root / 'seo-guide.md'}."
        )
    if context.stage == "image-maker":
        if context.keyword is not None:
            prompt += (
                " Read the canonical source-project inputs at "
                f"{context.root / 'research' / f'{context.keyword}.md'}, "
                f"{context.root / 'drafts' / f'{context.keyword}.md'}, and "
                f"{context.root / 'image-style-guide.md'}."
            )
        prompt += (
            " Preserve every [IMAGE:] marker's visual_slot_id, visual_intent, asset_type, "
            "required_by, source_policy, subject_scope, section, and fallback exactly. "
            "Create one real, non-empty body image file per marker and declare it in "
            "image-map.md, plus a separate thumbnail. Never replace a marker with a "
            "text_only fallback, omit its file, or change its asset_type."
        )
    if context.stage == "content-assembler":
        if context.keyword is not None:
            prompt += (
                " Read the canonical source-project inputs at "
                f"{context.root / 'research' / f'{context.keyword}.md'}, "
                f"{context.root / 'drafts' / f'{context.keyword}.md'}, and "
                f"{context.root / 'assets' / context.keyword / 'image-map.md'}, plus the "
                f"actual files under {context.root / 'assets' / context.keyword}."
            )
        prompt += (
            " Produce only the four final Markdown files. Do not create or declare a "
            "workflow manifest; the trusted runner creates it after promotion."
        )
    if context.selection_context is not None:
        prompt += " Selection context JSON: " + json.dumps(
            context.selection_context.as_json(), ensure_ascii=False, sort_keys=True
        ) + "."
    if context.q1_feedback is not None:
        prompt += (
            " Repair the prior Q1 failure using this safe feedback: "
            f"{safe_q1_feedback(context.q1_feedback)}"
        )
    return prompt


def stage_command(
    *,
    codex_binary: str,
    profile: str,
    schema_path: Path,
    output_path: Path,
    staging_root: Path,
    prompt: str,
) -> list[str]:
    return [
        codex_binary,
        "exec",
        "--strict-config",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--profile",
        profile,
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "--cd",
        str(staging_root),
        "--skip-git-repo-check",
        prompt,
    ]


__all__ = ["stage_command", "stage_prompt"]
