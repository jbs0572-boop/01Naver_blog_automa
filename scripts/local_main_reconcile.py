"""Safely back up a divergent, dirty macOS main and list every local-only commit.

NO checkout, reset, rebase, cherry-pick, remote write, fetch, or force push.
This program CANNOT run on the user's Mac from GitHub Actions: invoke locally.
Outputs may contain private code/secrets. Never upload the backup folder.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

AUGUST = {
    "8aa1a91": ("retired_or_replaced", "Old beta Notion write exception absent by design"),
    "3c5d34a": ("partly_superseded", "Offline review replaces old seven-stage writes"),
    "3eeb123": ("not_applicable", "Former content generator/Q1 retry no longer exists"),
    "765df54": ("retired", "External dashboard retry is not in active v2"),
    "c24d4dd": ("replaced", "v2 takes a structured title, not a tagged block"),
    "eb49caf": ("retired", "Old unfinished title tag parser not used"),
    "c3e19a7": ("partly_reflected", "Read-only Naver API search exists; browser research deferred"),
}


class ReconcileError(RuntimeError):
    pass


def git(repo: Path, *args: str, allow_failure: bool = False) -> bytes:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env)
    if result.returncode and not allow_failure:
        raise ReconcileError("Git query failed (%s): %s" % (
            " ".join(args), result.stderr.decode("utf8", "replace")[-600:]))
    return result.stdout if not result.returncode else b""


def text(raw: bytes) -> str:
    """Only for conventional text command output, NOT nul-delimited filenames."""
    return raw.decode("utf8", "surrogateescape").strip()


def path_text(raw: bytes) -> str:
    """Preserve every byte of an opaque Git path, including leading/trailing spaces."""
    return raw.decode("utf8", "surrogateescape")


def sha(path: Path) -> str:
    dig = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            dig.update(block)
    return dig.hexdigest()


def manifest(root: Path) -> dict:
    out = {}
    for item in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        name = item.relative_to(root).as_posix()
        if item.is_symlink():
            target = os.readlink(item)
            out[name] = {"type": "symlink", "target": target,
                         "sha256": hashlib.sha256(target.encode("utf8", "surrogateescape")).hexdigest()}
        elif item.is_file():
            out[name] = {"type": "file", "sha256": sha(item), "size": item.stat().st_size}
    return out


def blob(repo: Path, rev: str, path: str) -> str | None:
    raw = git(repo, "rev-parse", rev + ":" + path, allow_failure=True)
    return text(raw) if raw else None


def raw_diff(repo: Path, *, staged: bool = False) -> bytes:
    """Never run repository-configured external diff or non-restorable textconv."""
    flags = ("diff", "--no-textconv", "--no-ext-diff", "--binary")
    return git(repo, *flags, *(("--cached",) if staged else ()), "HEAD")


def untracked_fingerprints(repo: Path) -> dict[str, str]:
    paths = [path_text(x) for x in git(
        repo, "ls-files", "--others", "--exclude-standard", "-z"
    ).split(b"\x00") if x]
    result: dict[str, str] = {}
    for name in paths:
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or not name:
            raise ReconcileError("Unsafe untracked path")
        source = repo / rel
        if source.is_symlink():
            result[name] = "symlink:" + hashlib.sha256(
                os.readlink(source).encode("utf8", "surrogateescape")
            ).hexdigest()
        elif source.is_dir():
            # Nested Git worktrees collapse to ONE ls-files directory entry.
            # Explicitly fail rather than silently drop their private files.
            raise ReconcileError("Untracked directory needs its own full backup: " + repr(name))
        elif source.is_file():
            result[name] = "file:" + sha(source)
        else:
            raise ReconcileError("Untracked file missing or not a regular file: " + repr(name))
    return result


def repo_state(repo: Path) -> dict:
    status = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    tracked = raw_diff(repo)
    staged = raw_diff(repo, staged=True)
    index = git(repo, "ls-files", "--stage", "-z")
    return {
        "checked_out": text(git(repo, "symbolic-ref", "--short", "HEAD")),
        "main": text(git(repo, "rev-parse", "refs/heads/main")),
        "head": text(git(repo, "rev-parse", "HEAD")),
        "working_status_sha256": hashlib.sha256(status).hexdigest(),
        "tracked_bytes_patch_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_bytes_patch_sha256": hashlib.sha256(staged).hexdigest(),
        "index_entries_sha256": hashlib.sha256(index).hexdigest(),
        "untracked_sha256": untracked_fingerprints(repo),
    }


def commit_report(repo: Path, commit: str, remote: str, cherry: dict) -> dict:
    stamp, _, subject = text(git(repo, "show", "-s", "--format=%aI%x09%s", commit)).partition("\t")
    raw = git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r",
              "--root", commit, "-z")
    parts = raw.split(b"\x00")
    idx, files = 0, []
    while idx < len(parts) and parts[idx]:
        operation = text(parts[idx]); idx += 1
        if not re.fullmatch(r"[ACDMRTUXB][0-9]*", operation):
            raise ReconcileError("Unexpected Git diff-tree status: " + operation)
        n = 2 if operation[0] in "RC" else 1
        paths = [path_text(parts[idx + j]) for j in range(n)]
        idx += n
        path = paths[-1]
        remote_blob, old_blob = blob(repo, remote, path), blob(repo, commit, path)
        history = text(git(repo, "log", "-n", "2", "--format=%h %as %s",
                           remote, "--", path))
        files.append({
            "operation": operation, "paths": paths,
            "remote_path_exists": remote_blob is not None,
            "byte_identical_at_local_commit": remote_blob is not None and remote_blob == old_blob,
            "remote_path_recent_commits": history.splitlines() if history else [],
        })
    august = next((v for prefix, v in AUGUST.items() if commit.startswith(prefix)), None)
    decision = august[0] if august else "NEEDS_SEMANTIC_REVIEW"
    rationale = (august[1] + " (PR #12 interpretation; verify against current main)"
                 if august else "Same path or patch alone does not prove equal behavior")
    return {
        "sha": commit, "short": commit[:10], "date": stamp, "subject": subject,
        "files": files, "patch_equivalence": cherry.get(commit, "unknown"),
        "classification": decision, "reason": rationale,
    }


def audit(repo: Path, remote: str = "origin/main") -> dict:
    local = text(git(repo, "rev-parse", "refs/heads/main"))
    origin = text(git(repo, "rev-parse", remote + "^{commit}"))
    merge_base = text(git(repo, "merge-base", "refs/heads/main", remote))
    ahead = int(text(git(repo, "rev-list", "--count", remote + "..refs/heads/main")))
    behind = int(text(git(repo, "rev-list", "--count", "refs/heads/main.." + remote)))
    commits = text(git(repo, "rev-list", "--reverse",
                       remote + "..refs/heads/main")).splitlines()
    if len(commits) != ahead:
        raise ReconcileError("Local-only commit inventory is incomplete")
    patch_map = {}
    for line in text(git(repo, "cherry", "-v", remote, "refs/heads/main")).splitlines():
        match = re.match(r"([+-])\s+([0-9a-f]{40})\b", line)
        if match:
            patch_map[match[2]] = "equivalent_patch" if match[1] == "-" else "different_patch"
    all_commits = [commit_report(repo, item, remote, patch_map) for item in commits]
    return {
        "local_main": local, "remote_ref": remote, "remote_main": origin,
        "merge_base": merge_base, "ahead": ahead, "behind": behind,
        "count": len(all_commits), "commits": all_commits,
        "previously_reviewed_august": sorted(
            key for key in AUGUST if any(x["sha"].startswith(key) for x in all_commits)),
        "semantic_audit_complete": all(
            x["classification"] != "NEEDS_SEMANTIC_REVIEW" for x in all_commits),
        "warning": "Path/commit equivalence is not behavioral equivalence; new commits are unreviewed.",
    }


def audit_markdown(data: dict) -> str:
    output = [
        "# Local main versus origin/main — read-only commit inventory", "",
        "Local SHA: " + data["local_main"],
        "Remote SHA: " + data["remote_main"],
        "Common ancestor: " + data["merge_base"],
        "Divergence: %s ahead / %s behind" % (data["ahead"], data["behind"]),
        "Known August SHA prefixes found: %s/7" % len(data["previously_reviewed_august"]),
        "", "| Short SHA | Subject | Files | Git patch | Review classification |",
        "|---|---|---:|---|---|",
    ]
    for row in data["commits"]:
        safe_subject = row["subject"].replace("|", " / ").replace("\n", " ")
        output.append("| %s | %s | %s | %s | %s |" % (
            row["short"], safe_subject, len(row["files"]),
            row["patch_equivalence"], row["classification"]))
    output += [
        "", "## Important limitations",
        "", "- The seven August conclusions are inherited from PR #12.",
        "- Other commits MUST be behaviorally compared with current implementation and tests.",
        "- Git cherry '-' only proves equivalent patch content.",
        "- No local-only commit is automatically cherry-picked, merged or pushed.",
        "- No snapshot/backup data should be uploaded to GitHub.",
        "", "## Required human/Codex review",
        "", "For each NEEDS_SEMANTIC_REVIEW commit assign:",
        "already_implemented / partly_implemented / not_implemented / retired.",
        "For needed missing behavior create a new worktree from origin/main,",
        "implement isolated changes, test, then open a separate PR.",
    ]
    return "\n".join(output) + "\n"


def untracked_archive(repo: Path, output: Path, expected: dict[str, str]) -> int:
    """Archive every listed nonignored file and verify decoded tar contents."""
    names = list(untracked_fingerprints(repo))
    if set(names) != set(expected):
        raise ReconcileError("Untracked files changed before archive")
    with tarfile.open(output, "w:gz") as archive:
        for name in names:
            relative = Path(name)
            path = repo / relative
            if path.is_dir() and not path.is_symlink():
                raise ReconcileError("Untracked nested directory cannot be silently omitted")
            archive.add(path, arcname=(Path("untracked") / relative).as_posix(),
                        recursive=False)
    found: dict[str, str] = {}
    with tarfile.open(output, "r:gz") as archive:
        for member in archive.getmembers():
            name = member.name
            if not name.startswith("untracked/"):
                raise ReconcileError("Unexpected path inside untracked backup")
            path = name[len("untracked/"):]
            if member.issym():
                value = "symlink:" + hashlib.sha256(
                    member.linkname.encode("utf8", "surrogateescape")
                ).hexdigest()
            elif member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ReconcileError("Could not decode a stored untracked file")
                digest = hashlib.sha256()
                with stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                value = "file:" + digest.hexdigest()
            else:
                raise ReconcileError("Untracked archive contains non-file entry: " + repr(name))
            if path in found:
                raise ReconcileError("Duplicate path in untracked archive")
            found[path] = value
    if found != expected:
        raise ReconcileError("Untracked archive content differs from pre-backup files")
    return len(names)


def verify_restorable_bundle(repo: Path, bundle: Path, local_sha: str) -> str:
    """An isolated bundle import + object fsck, not merely git bundle verify."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    header = subprocess.run(
        ["git", "-C", str(repo), "bundle", "verify", str(bundle)],
        capture_output=True, env=env,
    )
    if header.returncode:
        raise ReconcileError("Git bundle header failed verification: " + text(header.stderr))
    with tempfile.TemporaryDirectory(prefix="inspect-bundle-", dir=bundle.parent) as temp:
        bare = Path(temp) / "repo.git"
        init = subprocess.run(["git", "init", "--bare", "-q", str(bare)],
                              capture_output=True, env=env)
        if init.returncode:
            raise ReconcileError("Could not create isolated Git bundle verifier")
        imported = subprocess.run(
            ["git", "-C", str(bare), "bundle", "unbundle", str(bundle)],
            capture_output=True, env=env,
        )
        if imported.returncode:
            raise ReconcileError("Git bundle payload failed isolated import: "
                                 + text(imported.stderr))
        refs = imported.stdout.decode("utf8", "replace").splitlines()
        if not refs or not any(line.startswith(local_sha + " ") for line in refs):
            raise ReconcileError("Imported bundle omitted the original local main")
        for line in refs:
            object_id = line.split(" ", 1)[0]
            check = subprocess.run(
                ["git", "-C", str(bare), "cat-file", "-e", object_id + "^{object}"],
                capture_output=True, env=env,
            )
            if check.returncode:
                raise ReconcileError("Referenced bundle object missing: " + object_id)
        fsck = subprocess.run(
            ["git", "-C", str(bare), "fsck", "--full", "--strict", "--no-reflogs"],
            capture_output=True, env=env,
        )
        if fsck.returncode:
            raise ReconcileError("Imported Git objects failed fsck: " + text(fsck.stderr))
    return ("header: VERIFIED\npayload import: VERIFIED\n"
            "objects: VERIFIED BY FSCK\nreferences: " + str(len(refs)) + "\n")


def backup(repo: Path, snapshot: Path, destination: Path, expected_main: str,
           remote: str = "origin/main") -> Path:
    repo = repo.expanduser().resolve(strict=True)
    snapshot = snapshot.expanduser().resolve(strict=True)
    destination = destination.expanduser().resolve()
    if not (repo / ".git").exists():
        raise ReconcileError("Specify the ORIGINAL Git working tree")
    if not snapshot.is_dir():
        raise ReconcileError("Original /tmp backup snapshot missing")
    if (destination.is_relative_to(repo) or destination == repo
            or destination.is_relative_to(snapshot) or snapshot.is_relative_to(destination)):
        raise ReconcileError("Backup destination cannot overlap the repo or the snapshot")
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", expected_main):
        raise ReconcileError("Expected local main SHA must be 7-40 hex characters")
    before = repo_state(repo)
    if before["checked_out"] != "main" or not before["main"].startswith(expected_main):
        raise ReconcileError("Wrong original checkout or local main moved; no backup started")
    summary = audit(repo, remote)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = destination / ("main-recovery-" + stamp)
    folder.mkdir(mode=0o700)
    try:
        snapshot_copy = folder / "old-temp-snapshot"
        shutil.copytree(snapshot, snapshot_copy, symlinks=True)
        copied = manifest(snapshot_copy)
        if copied != manifest(snapshot):
            raise ReconcileError("Old /tmp snapshot hash verification failed")
        (folder / "snapshot-sha256.json").write_text(
            json.dumps(copied, ensure_ascii=False, indent=2), encoding="utf8")
        bundle = folder / "all-git-refs.bundle"
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        b = subprocess.run(["git", "-C", str(repo), "bundle", "create",
                            str(bundle), "--all"], env=env, capture_output=True)
        if b.returncode:
            raise ReconcileError("Could not create Git ref bundle: " + text(b.stderr))
        bundle_report = verify_restorable_bundle(repo, bundle, before["main"])
        (folder / "bundle-verification.txt").write_text(
            bundle_report, encoding="utf8")
        (folder / "tracked-vs-HEAD.patch").write_bytes(raw_diff(repo))
        (folder / "staged-vs-HEAD.patch").write_bytes(raw_diff(repo, staged=True))
        (folder / "original-status.z").write_bytes(
            git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
        summary["untracked_count"] = untracked_archive(
            repo, folder / "untracked.tar.gz", before["untracked_sha256"])
        (folder / "AUDIT.md").write_text(audit_markdown(summary), encoding="utf8")
        (folder / "audit.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        after = repo_state(repo)
        if before != after:
            raise ReconcileError("Source main/worktree changed during backup")
        (folder / "source-state.json").write_text(
            json.dumps({"before": before, "after": after}, indent=2), encoding="utf8")
        files = manifest(folder)
        (folder / "backup-sha256.json").write_text(
            json.dumps(files, ensure_ascii=False, indent=2), encoding="utf8")
        folder.chmod(0o700)
        return folder
    except BaseException:
        (folder / "INCOMPLETE_BACKUP.txt").write_text(
            "Backup failed: inspect this partial directory manually.\n", encoding="utf8")
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path,
                        default=Path("/tmp/naver-main-reconcile-20261026"))
    parser.add_argument("--destination", type=Path,
                        default=Path.home() / "Documents" / "naver-blog-recovery")
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--remote", default="origin/main")
    options = parser.parse_args(argv)
    try:
        path = backup(options.repo, options.snapshot, options.destination,
                      options.expected_main, options.remote)
        report = json.loads((path / "audit.json").read_text(encoding="utf8"))
    except (OSError, ValueError, ReconcileError) as error:
        print("BACKUP/AUDIT FAILED:", error, file=sys.stderr)
        return 2
    print(json.dumps({
        "status": "BACKUP_BYTES_VERIFIED",
        "path": str(path), "ahead": report["ahead"], "behind": report["behind"],
        "commits_inventory": len(report["commits"]),
        "all_semantic_classifications_complete": report["semantic_audit_complete"],
        "privacy": "LOCAL_ONLY_DO_NOT_UPLOAD",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
