#!/usr/bin/env python3
"""Read-only inspection of Douyin AR processes and recent editor log signals on macOS."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


PROCESS_MARKERS = (
    ".app/Contents/MacOS/Douyin AR",
    "DouyinAR.app/Contents/MacOS",
)
SIGNAL_PATTERN = re.compile(
    r"Open project successfully|LoadProject|import_(?:start|end)|compiled success|compile.*(?:fail|error)|"
    r"scene.{0,60}?(?:fail|error)|process exit|close_start|AMGAssert|No available pass|"
    r"ETIMEDOUT|ECONNREFUSED|ENOTFOUND|effect_upload_result",
    re.IGNORECASE,
)
PROJECT_PATTERNS = (
    re.compile(r"--projectPath(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))"),
    re.compile(r"--project(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))"),
)


def redact(line: str) -> str:
    line = re.sub(r"https?://\S+", "<URL_REDACTED>", line)
    line = re.sub(r"(?i)(token|cookie|authorization|signature|uri)(\s*[:=]\s*)\S+", r"\1\2<REDACTED>", line)
    return line.rstrip()


def project_from_command(command: str) -> str:
    # ps emits unquoted argv: preserve spaces until the next flag, rather than
    # binding an editor for '/tmp/My Cat' to '/tmp/My'.
    match = re.search(r"--(?:projectPath|project)(?:=|\s+)(.*?)(?=\s+--|$)", command)
    if match:
        return match.group(1).strip().strip("\"'")
    for pattern in PROJECT_PATTERNS:
        match = pattern.search(command)
        if match:
            return next((part for part in match.groups() if part), "")
    return ""


def latest_editor_log(log_root: Path) -> Path | None:
    candidates = [
        path
        for path in log_root.rglob("*editor*.log")
        if path.is_file()
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=int, default=25, help="maximum recent log signal lines")
    parser.add_argument("--project", type=Path, help="expected project directory")
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("Runtime process inspection is currently implemented for macOS only.")
        return 0

    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,lstart=,command="],
        check=True,
        capture_output=True,
        text=True,
    )

    rows = []
    for line in result.stdout.splitlines():
        if not any(marker.lower() in line.lower() for marker in PROCESS_MARKERS):
            continue
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.{24})\s+(.*)$", line)
        if not match:
            continue
        pid, ppid, started, command = match.groups()
        project = project_from_command(command)
        role = "editor" if "--projectType=project" in command or "--index=" in command or project else "home"
        rows.append((int(pid), int(ppid), started.strip(), role, project))

    print(f"Douyin AR processes: {len(rows)}")
    for pid, ppid, started, role, project in rows:
        suffix = f", project={project}" if project else ""
        print(f"PID {pid}, PPID {ppid}, role={role}, started={started}{suffix}")

    by_project: dict[str, list[int]] = defaultdict(list)
    for pid, _ppid, _started, role, project in rows:
        if role == "editor" and project:
            by_project[str(Path(project).expanduser().resolve())].append(pid)

    duplicate_projects = {project: pids for project, pids in by_project.items() if len(pids) > 1}
    for project, pids in duplicate_projects.items():
        print(f"ERROR: duplicate editor writers for {project}: {pids}")

    expected_project = args.project.expanduser().resolve() if args.project else None
    editors = [row for row in rows if row[3] == "editor"]
    if expected_project:
        matched = [
            row
            for row in editors
            if row[4] and Path(row[4]).expanduser().resolve() == expected_project
        ]
        unknown = [row for row in editors if not row[4]]
        if matched:
            print(f"Expected project editor: matched PID(s) {[row[0] for row in matched]}")
            print("Binding: matched_by_process_argument")
        elif unknown:
            print("Binding: unconfirmed")
            print(
                "WARN: editor process does not expose a project path; confirm the window title and "
                f"latest Open project log before writing {expected_project}"
            )
        else:
            print("Binding: no_match")
            print(f"WARN: no editor process matched expected project {expected_project}")

    log_root = Path.home() / "Library" / "Application Support" / "DouyinAR" / "Logs"
    log = latest_editor_log(log_root) if log_root.is_dir() else None
    if not log:
        print("Latest editor log: not found")
    else:
        print(f"Latest editor log (not bound to expected project): {log}")
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            signals = []
            previous_key = ""
            for line in lines:
                if not SIGNAL_PATTERN.search(line):
                    continue
                # Emit signal names only: matching log lines can contain entire
                # export manifests, base64 icons, credentials and upload URLs.
                signal = SIGNAL_PATTERN.search(line).group(0)
                cleaned = next((code for code in ("ETIMEDOUT", "ECONNREFUSED", "ENOTFOUND", "effect_upload_result") if code.lower() == signal.lower()), "editor_lifecycle_or_import_signal")
                key = re.sub(r"^\[[^\]]+\]\s*", "", cleaned)
                if key == previous_key:
                    continue
                signals.append(cleaned)
                previous_key = key
            print(f"Recent signals ({min(len(signals), args.signals)} of {len(signals)}):")
            for line in signals[-args.signals :]:
                print("Signal (unbound log): " + line)
        except OSError as exc:
            print(f"WARN: cannot read latest log: {exc}")

    return 1 if duplicate_projects else 0


if __name__ == "__main__":
    sys.exit(main())
