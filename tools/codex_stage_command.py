from __future__ import annotations

import json
from pathlib import Path

from tools.model_presets import StageModelConfig
from tools.q1_feedback import guidance_for
from tools.runner_types import StageExecutionContext, safe_q1_feedback
from tools.topic_metadata import CreatorAdvisorCandidate, CreatorAdvisorSnapshot


def _canonical_output_paths(
    stage: str, keyword: str | None, research_artifact_path: str | None = None
) -> str:
    if stage == "topic-selector":
        if keyword is None:
            return (
                " Canonical output path rule: create exactly one file at "
                "research/topic-selection-<resolved keyword>.md, substituting the "
                "resolved keyword verbatim after the topic-selection- prefix."
            )
        return f" Canonical output path: research/topic-selection-{keyword}.md."
    if keyword is None:
        return ""
    if stage == "researcher":
        output = research_artifact_path or f"research/{keyword}.md"
        return f" Canonical output path: {output}."
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
    context: StageExecutionContext,
    instruction_path: Path,
    output_path: Path,
    research_artifact_path: str | None = None,
) -> str:
    agents_path = context.root / "AGENTS.md"
    execution_agents_path = context.root / "EXECUTION_AGENT.md"
    prompt = (
        f"Execute stage {context.stage} for run_id={context.run_id}, "
        f"topic_id={context.topic_id}, keyword={context.keyword or 'auto-topic'}. "
        f"Read {agents_path}, {execution_agents_path}, and {instruction_path}; treat "
        "only those three files as project instructions and follow all three. Mirror declared canonical relative artifact "
        "paths under the staging workspace's artifacts/ directory. Use the workspace root "
        "only for disposable helpers. Return canonical relative paths without the "
        "artifacts/ prefix in the structured result. Do not write to the source project. "
        f"Do not include the internal protocol file {output_path} in artifacts. "
        f"The trusted work directory {context.work_dir} is read-only; never write there. "
        "Do not call external write tools unless "
        "this stage permits it. If the stage cannot finish, return status=failed "
        "or blocked, execution=attempted, artifacts=[], and the concrete cause "
        "in message; never label a failed operation as validated or produced."
    )
    if context.stage not in {"notion-rider", "naver-rider"}:
        prompt += (
            " Create the required artifacts and report execution=produced. "
            "status describes validation outcome; execution describes artifact creation. "
            "Validation after creating artifacts does not change execution to validated. "
            " The structured result field run_status must be null for this producer "
            "stage; only notion-rider and naver-rider may set terminal run_status values."
        )
    canonical_paths = _canonical_output_paths(
        context.stage, context.keyword, research_artifact_path
    )
    if canonical_paths:
        literal_path_instruction = "Treat these paths as literal. Preserve every space and Unicode character in the keyword verbatim; do not slugify, normalize, transliterate, rename, or replace whitespace."
        prompt += canonical_paths + " " + literal_path_instruction
    if context.stage == "researcher":
        if context.keyword is not None:
            prompt += (
                " Read the canonical stage input from the source project at "
                f"{context.root / 'research' / f'topic-selection-{context.keyword}.md'}. "
                "Treat all source-project artifacts as input-only; never declare or "
                "reuse a source-project file as this stage's output. Write a fresh "
                "canonical research artifact under the staging workspace at "
                f"artifacts/{research_artifact_path or f'research/{context.keyword}.md'}, then declare "
                f"{research_artifact_path or f'research/{context.keyword}.md'}."
            )
        prompt += (
            " Do not spawn subagents. Do not invoke browser, network, or Aside CLI tools "
            "in this isolated stage. The host supplied the complete read-only evidence "
            "bundle, including original-page observations and its raw artifact path and "
            "hash. Evaluate the official lane first, then the supporting-visual lane in "
            "this process. Start the supporting-visual lane only after the official lane "
            "is complete. Record the supplied URL, access time, observed facts, and rights "
            "limitations in the research artifact. For every visual slot that the writer "
            "will express as an [IMAGE:] marker, choose an asset_type from the image-maker "
            "shared type list and require a real image file. Do not use text_only, "
            "comparison_table, checklist, or another non-shared type for an image marker; "
            "represent comparisons with side_by_side or chart and checklists with "
            "process_flow so marker metadata can remain identical through assembly."
        )
    if context.stage == "topic-selector" and context.keyword is None:
        prompt += (
            " Aside Browser capture is performed by the host before this isolated stage. "
            "Use the supplied read-only evidence; do not run browser commands or CLI updates. "
            '<aside-preflight guide="host-managed" session="host-capture" update="host-managed" />'
            " The only candidate source for automatic topic selection is the Naver Creator "
            "Advisor page named by the topic-selector instructions. From the supplied Aside "
            "observations, record the visible keyword exactly as shown, and save "
            "the raw observation in metadata/creator-advisor/<as_of_date>/<capture_id>.json. "
            "Before selecting, inspect the existing read-only selection files at "
            f"{context.root / 'research' / 'topic-selection-*.md'} and exclude duplicate "
            "and semantically equivalent candidates; never alter those existing files. "
            "Do not invent keywords, estimate missing values, submit, save, download, or "
            "otherwise write through the browser. If the page cannot be inspected, stop "
            "with a concrete access limitation."
        )
        selection = context.selection_context
        if selection is not None and selection.score_version is not None:
            prompt += (
                " Use the pinned topic feedback decision from the Selection context; "
                "do not substitute a score version or feedback manifest. "
            )
        if selection is not None and selection.snapshot_policy is not None:
            prompt += (
                f" Batch snapshot policy is {selection.snapshot_policy}. The exact snapshot path is "
                f"{selection.snapshot_path}. Ordered excluded keywords JSON: "
                f"{json.dumps(selection.excluded_keywords, ensure_ascii=False)}. Select and return exactly "
                "one candidate keyword verbatim. "
            )
            if selection.snapshot_policy == "reuse_only":
                prompt += (
                    "Reuse the supplied immutable snapshot only. Do not open or read "
                    "Creator Advisor again and do not create or declare metadata output."
                )
            else:
                prompt += (
                    "Capture Creator Advisor at most once and create exactly the declared "
                    "snapshot path when it is not already supplied."
                )
                if selection.capture_id is not None:
                    snapshot_contract = CreatorAdvisorSnapshot(
                        as_of_date=selection.as_of_date,
                        captured_at="<KST ISO-8601>",
                        capture_id=selection.capture_id,
                        candidates=(CreatorAdvisorCandidate("<visible keyword>", 1),),
                    ).as_json()
                    prompt += (
                        " Write the snapshot with exactly this canonical JSON shape. "
                        "Replace the timestamp and candidate placeholders with observed "
                        "values, and repeat the candidate object for every visible "
                        "candidate without renaming or omitting fields. "
                        "<creator-advisor-snapshot-contract>\n"
                        f"{json.dumps(snapshot_contract, ensure_ascii=False, sort_keys=True)}\n"
                        "</creator-advisor-snapshot-contract>"
                    )
    if context.stage == "writer" and context.keyword is not None:
        source_research_path = research_artifact_path or f"research/{context.keyword}.md"
        prompt += (
            " Read the canonical source-project inputs at "
            f"{context.root / source_research_path}, "
            f"{context.root / 'style-guide.md'}, and {context.root / 'seo-guide.md'}."
        )
    if context.stage == "image-maker":
        if context.keyword is not None:
            source_research_path = research_artifact_path or f"research/{context.keyword}.md"
            prompt += (
                " Read the canonical source-project inputs at "
                f"{context.root / source_research_path}, "
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
            source_research_path = research_artifact_path or f"research/{context.keyword}.md"
            prompt += (
                " Read the canonical source-project inputs at "
                f"{context.root / source_research_path}, "
                f"{context.root / 'drafts' / f'{context.keyword}.md'}, and "
                f"{context.root / 'assets' / context.keyword / 'image-map.md'}, plus the "
                f"actual files under {context.root / 'assets' / context.keyword}."
            )
            prompt += (
                " The stage instruction names logical final/ paths, but this isolated "
                "workspace requires the artifacts/ prefix: write every output under "
                f"artifacts/final/{context.keyword}.md, "
                f"artifacts/final/{context.keyword}-naver-layout.md, "
                f"artifacts/final/{context.keyword}-naver-copy.md, and "
                f"artifacts/final/{context.keyword}-naver-input.md; never write to "
                "final/ directly."
            )
        prompt += (
            " Produce only the four final Markdown files. Do not create or declare a "
            "workflow manifest; the trusted runner creates it after promotion. "
            "Both naver-copy and naver-input must use the canonical bracket grammar: "
            "every TITLE, TEXT, LIST, TABLE, ALT, and CAPTION block must have its matching "
            "closing tag, and one-line forms must close on that same line. Every HEADING "
            "must also close with [/HEADING]. IMAGE, BLANK, ITEM, and ROW must follow "
            "content-assembler.md exactly. IMAGE tags may contain only file, alt, and "
            "representative attributes (an optional evidence attribute is accepted only "
            "for internal normalization); do not put visual-slot or source metadata in "
            "canonical IMAGE tags. LIST/TABLE containers must not contain "
            "blank lines or any content outside ITEM/ROW children."
        )
    if context.selection_context is not None:
        prompt += (
            " Selection context JSON: "
            + json.dumps(
                context.selection_context.as_json(), ensure_ascii=False, sort_keys=True
            )
            + "."
        )
    if context.q1_feedback is not None:
        prompt += (
            " Repair the prior Q1 failure using this safe feedback: "
            f"{safe_q1_feedback(context.q1_feedback)}"
        )
    if context.q1_preflight_codes:
        guidance = [guidance_for(code) for code in context.q1_preflight_codes]
        prompt += " Active Q1 preflight guidance: " + " ".join(
            value for value in guidance if value
        )
    return prompt


def stage_command(
    *,
    codex_binary: str,
    profile: str,
    schema_path: Path,
    output_path: Path,
    workspace_root: Path,
    prompt: str,
    model_config: StageModelConfig | None = None,
) -> list[str]:
    command = [
        codex_binary,
        "exec",
        "--strict-config",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--profile",
        profile,
        "--sandbox",
        "workspace-write",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "--cd",
        str(workspace_root),
        "--skip-git-repo-check",
    ]
    if model_config is not None:
        command.extend(
            [
                "--model",
                model_config.model,
                "--config",
                f'model_reasoning_effort="{model_config.reasoning_effort}"',
            ]
        )
    command.append(prompt)
    return command


__all__ = ["stage_command", "stage_prompt"]
