"""Evidence-first blog draft pipeline (offline, no account or publishing access).

The input is an editor-curated source pack, NOT an executable instruction set.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

VERSION = "2.0.0-preview"
MAX_SOURCES = 20
MAX_CLAIMS = 60
V2_WORKFLOWS = ("evidence-packet", "review-export", "naver-search-discovery", "editorial-preview")


class ContentError(ValueError):
    """An input, integrity or explicit-approval contract failed."""


def canonical(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_text(value: object, name: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContentError(f"{name}: nonempty text up to {maximum} characters required")
    if any(ord(char) < 32 for char in value):
        raise ContentError(f"{name}: control characters are prohibited")
    return value.strip()


def verified_url(value: object) -> str:
    value = safe_text(value, "source URL", maximum=2048)
    try:
        url = urlsplit(value)
        host = url.hostname
        port = url.port
    except ValueError as error:
        raise ContentError("malformed source URL") from error
    if (url.scheme != "https" or not host or url.username or url.password
            or port not in {None, 443} or host.lower() in {"localhost", "localhost.localdomain"}
            or host.endswith((".local", ".internal"))):
        raise ContentError("only public HTTPS evidence URLs without credentials are permitted")
    # URLs are references only; never fetch source pack URLs as instructions.
    import ipaddress
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ContentError("IP-address evidence URLs are prohibited")
    if "." not in host or not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
        raise ContentError("source URL requires a public DNS name")
    return value


def markdown_url(url: str) -> str:
    """Escape Markdown link delimiters even when the HTTPS URL is otherwise valid."""
    return quote(url, safe=":/?&=#@+$;,%-._~")


def iso_date(value: object, label: str) -> date:
    text = safe_text(value, label, maximum=10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise ContentError(f"{label}: YYYY-MM-DD required") from error
    if parsed.isoformat() != text:
        raise ContentError(f"{label}: YYYY-MM-DD required")
    return parsed


def validate_pack(raw: object) -> dict:
    """Structural gates only; a human must still verify claimed source accuracy."""
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise ContentError("source pack requires schema_version=2")
    title = safe_text(raw.get("title"), "title", maximum=90)
    audience = safe_text(raw.get("audience"), "audience", maximum=160)
    angle = safe_text(raw.get("angle"), "angle", maximum=360)
    as_of = iso_date(raw.get("as_of"), "as_of")
    sources = raw.get("sources")
    claims = raw.get("claims")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ContentError("sources: require 1..20 entries")
    if not isinstance(claims, list) or not 2 <= len(claims) <= MAX_CLAIMS:
        raise ContentError("claims: require 2..60 entries")
    seen_sources: set[str] = set()
    safe_sources: list[dict] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ContentError("source entry must be an object")
        sid = safe_text(source.get("id"), "source.id", maximum=32)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sid) or sid in seen_sources:
            raise ContentError("source IDs must be unique safe identifiers")
        seen_sources.add(sid)
        published = iso_date(source.get("published_at"), "source.published_at")
        if published > as_of:
            raise ContentError("source publication is later than the declared cutoff")
        safe_sources.append({
            "id": sid,
            "title": safe_text(source.get("title"), "source.title", maximum=180),
            "url": verified_url(source.get("url")),
            "published_at": published.isoformat(),
        })
    safe_claims: list[dict] = []
    seen_claims: set[str] = set()
    seen_text: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            raise ContentError("claim entry must be an object")
        cid = safe_text(claim.get("id"), "claim.id", maximum=32)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cid) or cid in seen_claims:
            raise ContentError("claim IDs must be unique safe identifiers")
        seen_claims.add(cid)
        sid = safe_text(claim.get("source_id"), "claim.source_id", maximum=32)
        if sid not in seen_sources:
            raise ContentError(f"claim {cid} refers to unknown source: {sid}")
        status = claim.get("evidence_level")
        if status not in {"official", "secondary", "interpretation"}:
            raise ContentError(f"claim {cid} evidence_level must be explicitly classified")
        statement = safe_text(claim.get("text"), "claim.text", maximum=650)
        section = safe_text(claim.get("section"), "claim.section", maximum=70)
        normalized = re.sub(r"\s+", "", statement).casefold()
        if normalized in seen_text:
            raise ContentError(f"duplicate claim text: {cid}")
        seen_text.add(normalized)
        safe_claims.append({"id": cid, "source_id": sid, "evidence_level": status,
                            "text": statement, "section": section})
    return {"schema_version": 2, "title": title, "audience": audience,
            "angle": angle, "as_of": as_of.isoformat(), "sources": safe_sources,
            "claims": safe_claims}


def render_draft(pack: dict) -> str:
    """Make an auditable baseline, not an invented AI-generated article."""
    def clean(text: str) -> str:
        return html.escape(text, quote=False).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")

    sources = {item["id"]: item for item in pack["sources"]}
    lines = [f"# {clean(pack['title'])}", "",
             f"*기준일: {pack['as_of']} · 대상 독자: {clean(pack['audience'])}*", "",
             clean(pack["angle"]), "",
             "> 이 초안은 입력된 자료를 구조화한 것입니다. 원문 확인과 게시 승인이 필요합니다.", ""]
    sections: dict[str, list[dict]] = {}
    for claim in pack["claims"]:
        sections.setdefault(claim["section"], []).append(claim)
    for section, items in sections.items():
        lines.extend([f"## {clean(section)}", ""])
        for claim in items:
            src = sources[claim["source_id"]]
            qualifier = "[자료 기반 해석] " if claim["evidence_level"] == "interpretation" else ""
            lines.append(f"- {qualifier}{clean(claim['text'])} [근거: {claim['source_id']}]({markdown_url(src['url'])})")
        lines.append("")
    lines.extend(["## 자료 출처", ""])
    for src in pack["sources"]:
        lines.append(f"- {src['id']}: [{clean(src['title'])}]({markdown_url(src['url'])}) ({src['published_at']})")
    lines.extend(["", "## 편집자 점검", "",
                  "- 원문 직접 확인 / 날짜·저작권·사실 관계 / 독자에게 추가하는 정보 확인",
                  "- 사진 및 공개 글에 대한 최종 동의 확인", ""])
    return "\n".join(lines)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ContentError(f"cannot read JSON: {path}") from error


def verify_bundle(folder: Path) -> dict:
    """Always rehash both immutable inputs before approval or export."""
    if not folder.is_dir() or folder.is_symlink():
        raise ContentError("bundle must be a real directory")
    for filename in ("manifest.json", "evidence.json", "draft.md"):
        if (folder / filename).is_symlink():
            raise ContentError("bundle symlinks are forbidden")
    manifest = _read_json(folder / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION:
        raise ContentError("manifest version invalid")
    pack = validate_pack(_read_json(folder / "evidence.json"))
    # Digest matches alone do not validate mutable status/count metadata.
    # Never accept a forged claim that this offline packet was published.
    if (
        manifest.get("state") != "review_required"
        or manifest.get("external_writes") is not False
        or manifest.get("as_of") != pack["as_of"]
        or type(manifest.get("source_count")) is not int
        or manifest["source_count"] != len(pack["sources"])
        or type(manifest.get("claim_count")) is not int
        or manifest["claim_count"] != len(pack["claims"])
    ):
        raise ContentError("manifest review state or evidence metadata invalid")
    if digest(canonical(pack)) != manifest.get("input_sha256"):
        raise ContentError("evidence digest mismatch")
    try:
        content = (folder / "draft.md").read_bytes()
    except OSError as error:
        raise ContentError("draft is unreadable") from error
    if digest(content) != manifest.get("draft_sha256") or content != render_draft(pack).encode("utf-8"):
        raise ContentError("draft integrity check failed")
    expected_id = "RUN-" + digest(canonical(pack))[:24]
    if manifest.get("run_id") != expected_id:
        raise ContentError("run ID mismatch")
    return manifest


def build_bundle(input_path: Path, output_root: Path) -> Path:
    pack = validate_pack(_read_json(input_path))
    source_digest = digest(canonical(pack))
    run_id = "RUN-" + source_digest[:24]
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / run_id
    if target.exists() or target.is_symlink():
        existing = verify_bundle(target)
        if existing["input_sha256"] != source_digest:
            raise ContentError("existing bundle does not match source pack")
        return target
    tmp = Path(tempfile.mkdtemp(prefix=".blog-v2-", dir=output_root))
    try:
        draft = render_draft(pack).encode("utf-8")
        manifest = {"version": VERSION, "run_id": run_id, "input_sha256": source_digest,
                    "draft_sha256": digest(draft), "as_of": pack["as_of"],
                    "source_count": len(pack["sources"]),
                    "claim_count": len(pack["claims"]), "state": "review_required",
                    "external_writes": False}
        (tmp / "evidence.json").write_bytes(canonical(pack) + b"\n")
        (tmp / "draft.md").write_bytes(draft)
        (tmp / "manifest.json").write_bytes(canonical(manifest) + b"\n")
        verify_bundle(tmp)
        if target.exists() or target.is_symlink():
            raise ContentError("another process already created this run")
        os.rename(tmp, target)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return target


def approve_bundle(folder: Path, reviewer: str, expected_digest: str) -> Path:
    reviewer = safe_text(reviewer, "reviewer", maximum=80)
    manifest = verify_bundle(folder)
    if expected_digest != manifest["draft_sha256"]:
        raise ContentError("reviewed digest does not match current draft")
    approval_path = folder / "approval.json"
    approval = {"run_id": manifest["run_id"], "draft_sha256": expected_digest,
                "input_sha256": manifest["input_sha256"], "reviewed_by": reviewer,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "scope": "local_export_only", "allows_naver_publish": False}
    try:
        with approval_path.open("x", encoding="utf-8") as handle:
            json.dump(approval, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise ContentError("already approved; immutable review record") from error
    return approval_path


def export_copy(folder: Path) -> Path:
    manifest = verify_bundle(folder)
    approval_file = folder / "approval.json"
    if approval_file.is_symlink():
        raise ContentError("approval symlinks are forbidden")
    approval = _read_json(approval_file)
    if not isinstance(approval, dict) or any((
        approval.get("run_id") != manifest["run_id"],
        approval.get("draft_sha256") != manifest["draft_sha256"],
        approval.get("input_sha256") != manifest["input_sha256"],
        approval.get("scope") != "local_export_only",
        approval.get("allows_naver_publish") is not False,
        not isinstance(approval.get("reviewed_by"), str) or not approval.get("reviewed_by"),
    )):
        raise ContentError("approval record is missing, invalid or stale")
    copy_path = folder / "naver-copy.md"
    content = (folder / "draft.md").read_bytes()
    # Verify the exact bytes subsequently written, not only an earlier read.
    if digest(content) != manifest["draft_sha256"]:
        raise ContentError("draft changed during export; aborting")
    if copy_path.is_symlink():
        raise ContentError("export symlinks are forbidden")
    if copy_path.exists():
        if copy_path.read_bytes() != content:
            raise ContentError("existing export differs; refuse overwrite")
        return copy_path
    try:
        with copy_path.open("xb") as handle:
            handle.write(content)
    except FileExistsError as error:
        raise ContentError("concurrent export exists") from error
    return copy_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Generate an evidence-based review packet (no external writes)")
    build.add_argument("--input", type=Path, required=True)
    build.add_argument("--output-root", type=Path, required=True)
    demo = sub.add_parser("demo", help="Build the committed deterministic example")
    demo.add_argument("--output-root", type=Path, required=True)
    verify = sub.add_parser("verify", help="Check bundle integrity")
    verify.add_argument("--folder", type=Path, required=True)
    approve = sub.add_parser("approve", help="Human local-export approval, NOT blog posting authorization")
    approve.add_argument("--folder", type=Path, required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--digest", required=True)
    export = sub.add_parser("export", help="Create manual copy file only after approval")
    export.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            path = Path(__file__).resolve().parents[1] / "examples" / "blog-v2-sourcepack.json"
            value: object = {"bundle": str(build_bundle(path, args.output_root)), "status": "review_required"}
        elif args.command == "build":
            value = {"bundle": str(build_bundle(args.input, args.output_root)), "status": "review_required"}
        elif args.command == "verify":
            value = verify_bundle(args.folder)
        elif args.command == "approve":
            value = {"approval": str(approve_bundle(args.folder, args.reviewer, args.digest))}
        else:
            value = {"manual_copy": str(export_copy(args.folder)), "posted": False}
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except ContentError as error:
        parser.exit(status=2, message=f"blog-v2: {error}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
