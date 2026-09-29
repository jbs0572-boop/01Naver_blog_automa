from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.blog_stats_import import BlogStatsImportRequest, import_blog_stats
from tools.contract_types import ContractError
from tools.publication_metrics_link import (
    CURRENT_SCORE_VERSION,
    PublicationAttributionRequest,
    link_publication,
)
from tools.topic_feedback_config import (
    create_rollback,
    load_rollout_config,
)
from tools.topic_feedback_import_cli import ImportCliRequest, run_import_capture
from tools.topic_feedback_policy import load_registry
from tools.topic_feedback_replay import ReplayRequest, replay
from tools.topic_feedback_scoring_io import (
    canonical_json,
    evaluate_payload,
    rank_payload,
)
from tools.topic_performance import compute_cohorts, serialize_cohorts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="topic-feedback")
    commands = parser.add_subparsers(dest="command", required=True)
    sources = commands.add_parser("sources")
    _ = sources.add_argument("--config", type=Path, required=True)
    _ = sources.add_argument("--format", choices=("json",), required=True)
    publication = commands.add_parser("link-publication")
    _ = publication.add_argument("--root", type=Path, required=True)
    _ = publication.add_argument("--run-id", required=True)
    _ = publication.add_argument("--blog-post-id")
    _ = publication.add_argument("--published-at", required=True)
    _ = publication.add_argument("--captured-at")
    _ = publication.add_argument(
        "--source",
        choices=("current-run", "legacy-import"),
        default="current-run",
    )
    _ = publication.add_argument("--topic-id")
    _ = publication.add_argument("--keyword")
    _ = publication.add_argument("--artifact-digest")
    _ = publication.add_argument("--score-version", default=CURRENT_SCORE_VERSION)
    _ = publication.add_argument("--legacy-identity")
    _ = publication.add_argument("--target-blog-id")
    _ = publication.add_argument("--naver-post-url")
    _ = publication.add_argument("--url-rule-approval", type=Path)
    _ = publication.add_argument("--url-rule-approval-sha256")
    config_check = commands.add_parser("config-check")
    _ = config_check.add_argument("--root", type=Path, required=True)
    _ = config_check.add_argument("--config", type=Path, required=True)
    _ = config_check.add_argument("--explain", action="store_true")
    rollback = commands.add_parser("rollback")
    _ = rollback.add_argument("--root", type=Path, required=True)
    _ = rollback.add_argument("--config", type=Path, required=True)
    _ = rollback.add_argument("--rollback-id", required=True)
    _ = rollback.add_argument("--captured-at", required=True)
    import_capture = commands.add_parser("import")
    _ = import_capture.add_argument("--source", required=True)
    _ = import_capture.add_argument("--input", required=True)
    _ = import_capture.add_argument("--as-of-date", required=True)
    _ = import_capture.add_argument("--root", type=Path, required=True)
    _ = import_capture.add_argument("--dry-run", action="store_true")
    _ = import_capture.add_argument("--json", action="store_true", required=True)
    blog_stats = commands.add_parser("import-blog-stats")
    _ = blog_stats.add_argument("--input", type=Path, required=True)
    _ = blog_stats.add_argument("--owner-config", type=Path, required=True)
    _ = blog_stats.add_argument("--root", type=Path, required=True)
    _ = blog_stats.add_argument("--json", action="store_true", required=True)
    cohorts = commands.add_parser("cohorts")
    _ = cohorts.add_argument("--as-of", required=True)
    _ = cohorts.add_argument("--root", type=Path, required=True)
    _ = cohorts.add_argument("--json", action="store_true", required=True)
    rank = commands.add_parser("rank")
    _ = rank.add_argument("--root", type=Path, required=True)
    _ = rank.add_argument("--snapshot", type=Path, required=True)
    _ = rank.add_argument("--signals", type=Path)
    _ = rank.add_argument("--as-of", required=True)
    _ = rank.add_argument("--json", action="store_true", required=True)
    evaluate = commands.add_parser("evaluate")
    _ = evaluate.add_argument("--input", type=Path, required=True)
    _ = evaluate.add_argument("--json", action="store_true", required=True)
    replay_command = commands.add_parser("replay")
    _ = replay_command.add_argument("--root", type=Path, required=True)
    _ = replay_command.add_argument("--as-of", required=True)
    _ = replay_command.add_argument("--evidence-dir", type=Path, required=True)
    return parser


def _run(arguments: Sequence[str]) -> int:
    parsed = _parser().parse_args(arguments)
    match parsed.command:  # noqa: RUF100  # noqa: MATCH_OK
        case "sources":
            registry = load_registry(parsed.config)
            print(
                json.dumps(
                    registry.as_json(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        case "link-publication":
            source = parsed.source
            if source not in {"current-run", "legacy-import"}:
                raise ContractError("publication source is invalid")
            result = link_publication(
                PublicationAttributionRequest(
                    root=parsed.root,
                    run_id=parsed.run_id,
                    blog_post_id=parsed.blog_post_id,
                    published_at=parsed.published_at,
                    captured_at=parsed.captured_at or parsed.published_at,
                    source_identity=source,
                    topic_id=parsed.topic_id,
                    keyword=parsed.keyword,
                    artifact_digest=parsed.artifact_digest,
                    score_version=parsed.score_version,
                    legacy_identity=parsed.legacy_identity,
                    target_blog_id=parsed.target_blog_id,
                    naver_post_url=parsed.naver_post_url,
                    url_rule_approval=parsed.url_rule_approval,
                    url_rule_approval_sha256=parsed.url_rule_approval_sha256,
                )
            )
            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        case "config-check":
            pin = load_rollout_config(parsed.root, parsed.config)
            mutation = "true" if pin.selection_mutation else "false"
            artifacts = "true" if pin.new_feedback_artifacts_enabled else "false"
            fields = (
                f"active={pin.active_score_version}",
                f"shadow={pin.shadow_score_version}",
                f"selection_mutation={mutation}",
            )
            if pin.rollback_digest is not None:
                fields += (f"new_feedback_artifacts_enabled={artifacts}",)
            print(" ".join(fields))
            return 0
        case "rollback":
            result = create_rollback(
                parsed.root,
                parsed.config,
                parsed.rollback_id,
                parsed.captured_at,
            )
            print(
                json.dumps(
                    {
                        "path": str(result.path),
                        "digest": result.digest,
                        "active_score_version": result.pin.active_score_version,
                        "new_feedback_artifacts_enabled": result.pin.feedback_enabled,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        case "import":
            return run_import_capture(
                ImportCliRequest(
                    parsed.input,
                    parsed.source,
                    parsed.as_of_date,
                    parsed.root,
                    parsed.dry_run,
                )
            )
        case "import-blog-stats":
            result = import_blog_stats(
                BlogStatsImportRequest(
                    parsed.input,
                    parsed.owner_config,
                    parsed.root,
                )
            )
            print(
                json.dumps(
                    {
                        "count": len(result.snapshots),
                        "paths": list(result.paths),
                        "source_digest": result.source_digest,
                        "snapshots": list(result.snapshots),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        case "cohorts":
            print(serialize_cohorts(compute_cohorts(parsed.root, parsed.as_of)))
            return 0
        case "rank":
            print(
                canonical_json(
                    rank_payload(
                        parsed.root, parsed.snapshot, parsed.signals, parsed.as_of
                    )
                )
            )
            return 0
        case "evaluate":
            print(canonical_json(evaluate_payload(parsed.input)))
            return 0
        case "replay":
            _ = sys.stdout.buffer.write(
                replay(ReplayRequest(parsed.root, parsed.as_of, parsed.evidence_dir))
            )
            return 0
        case unreachable:
            raise ContractError(f"unknown topic feedback command: {unreachable}")


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        return _run(sys.argv[1:] if arguments is None else arguments)
    except ContractError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
