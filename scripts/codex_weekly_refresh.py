#!/usr/bin/env python3
"""Safely prepare or publish a Codex-led weekly notice refresh."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = ("data/projects.json", "data/sources.json")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=check, timeout=120,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def require_main() -> None:
    branch = git("branch", "--show-current").stdout.strip()
    if branch != "main":
        raise RuntimeError(f"Expected the main branch, found {branch or 'detached HEAD'}")


def changed_paths() -> set[str]:
    paths: set[str] = set()
    for args in (
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        paths.update(line for line in git(*args).stdout.splitlines() if line)
    return paths


def require_remote_at_head() -> None:
    git("fetch", "origin", "main")
    local = git("rev-parse", "HEAD").stdout.strip()
    remote = git("rev-parse", "origin/main").stdout.strip()
    if local != remote:
        raise RuntimeError("Local main differs from origin/main; resolve the Git state before publishing")


def check_data() -> None:
    projects_doc = json.loads((ROOT / DATA_FILES[0]).read_text(encoding="utf-8"))
    sources_doc = json.loads((ROOT / DATA_FILES[1]).read_text(encoding="utf-8"))
    for kind, entries in (("project", projects_doc.get("projects")), ("source", sources_doc.get("sources"))):
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{kind} list is missing or empty")
        ids: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
                raise ValueError(f"{kind} has an entry without an id")
            if entry["id"] in ids:
                raise ValueError(f"Duplicate {kind} id: {entry['id']}")
            ids.add(entry["id"])
            key = "sourceUrl" if kind == "project" else "url"
            url = entry.get(key)
            if kind == "project" and entry.get("recordType") == "official-source-index":
                continue
            parsed = urlparse(url or "")
            if not parsed.hostname or parsed.scheme not in ({"https"} if kind == "project" else {"http", "https"}):
                raise ValueError(f"Invalid {kind} URL for {entry['id']}: {url}")
    print(f"Validated {len(projects_doc['projects'])} projects and {len(sources_doc['sources'])} sources.")


def prepare() -> None:
    require_main()
    if changed_paths():
        raise RuntimeError("Working tree is not clean; leave local edits untouched and resolve them before this run")
    git("fetch", "origin", "main")
    git("merge", "--ff-only", "origin/main")
    check_data()
    print("Local main is clean and up to date.")


def publish() -> None:
    require_main()
    unexpected = changed_paths() - set(DATA_FILES)
    if unexpected:
        raise RuntimeError(f"Unexpected local changes; refusing to publish: {', '.join(sorted(unexpected))}")
    require_remote_at_head()
    scan = subprocess.run([sys.executable, str(ROOT / "scripts" / "weekly_scan.py")], cwd=ROOT)
    if scan.returncode:
        raise RuntimeError(f"Weekly scan failed with exit code {scan.returncode}")
    check_data()
    unexpected = changed_paths() - set(DATA_FILES)
    if unexpected:
        raise RuntimeError(f"Unexpected changes after scanning: {', '.join(sorted(unexpected))}")
    git("add", "--", *DATA_FILES)
    diff = git("diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        print("No data changes to publish.")
        return
    if diff.returncode != 1:
        raise RuntimeError("Could not inspect staged changes")
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    git("-c", "user.name=Codex Weekly Update", "-c", "user.email=codex@users.noreply.github.com",
        "commit", "-m", f"data: weekly official notice refresh {day}")
    git("push", "origin", "HEAD:main")
    print(f"Published the weekly data refresh for {day} to origin/main.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "prepare", "publish"))
    args = parser.parse_args()
    try:
        if args.command == "check":
            check_data()
        elif args.command == "prepare":
            prepare()
        else:
            publish()
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            print((error.stderr or error.stdout or str(error)).strip(), file=sys.stderr)
        else:
            print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
