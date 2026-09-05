#!/usr/bin/env python3
"""Read-only Douyin effect diagnostics. No upload, deletion, or automatic approval."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
EXCLUDE = {"Library", ".git", ".douyin-effect"}
LAYERS = ("source", "compile", "editor_preview", "phone_preview", "effect_detection", "release_materials")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def files(root, excluded=()):
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not (Path(base) / d).is_symlink())
        for name in sorted(names):
            p = Path(base) / name
            if not p.is_symlink() and p.is_file():
                yield p


def revision(project):
    h = hashlib.sha256()
    for p in files(project, EXCLUDE):
        h.update(str(p.relative_to(project)).encode() + b"\0")
        h.update(digest(p).encode() + b"\0")
    return h.hexdigest()


def inventory(project, limit=None, package=None):
    sizes = [(str(p.relative_to(project)), p.stat().st_size) for p in files(project)]
    total = sum(size for _, size in sizes)
    groups = {}
    for name, size in sizes:
        key = name.split("/")[0]
        groups[key] = groups.get(key, 0) + size
    return {"disk_bytes": total, "directories": groups,
            "largest_files": sorted(sizes, key=lambda x: x[1], reverse=True)[:15],
            "limit_bytes": limit, "over_limit": total > limit if limit is not None else None,
            "note": "Disk inventory is not the native packaged project size; limits must come from current UI.",
            "effect_package": {"bytes": package.stat().st_size, "sha256": digest(package)} if package else None}


def legacy(script, project, runtime=False):
    command = [sys.executable, str(HERE / script)]
    if runtime:
        command += ["--project", str(project)]
    else:
        command += [str(project)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    # Keep raw logs and arbitrary source strings out of machine reports.
    lines = result.stdout.splitlines()
    return {"exit_code": result.returncode,
            "errors": sum(x.startswith("ERROR:") for x in lines),
            "warnings": sum(x.startswith("WARN:") for x in lines),
            "summary": [x for x in lines if x.startswith(("Summary:", "Assets:", "PNG assets:", "Douyin AR processes:", "PID ", "Expected project editor:", "Binding:", "Signal (unbound log):"))],
            "detail_command": command}


def sequences(project):
    result = []
    for folder in sorted((project / "Assets").rglob("*.otextureseq.folder")):
        images = [p for p in files(folder) if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        result.append({"path": str(folder.relative_to(project)), "source_frame_count": len(images),
                       "runtime_frame_count": None, "runtime_status": "not_run",
                       "note": "Source count cannot prove native getFrameCount() or cached resource contents."})
    return result


def evidence(project, ledger, package=None):
    current = revision(project)
    data = json.loads(ledger.read_text()) if ledger.exists() else {"schema_version": 1, "records": []}
    if data.get("schema_version") != 1 or not isinstance(data.get("records"), list):
        raise ValueError("unsupported evidence ledger schema")
    records = data["records"]
    if any(not isinstance(r, dict) or "layer" not in r for r in records):
        raise ValueError("invalid evidence record")
    layers = {}
    for layer in LAYERS + ("submission", "review"):
        matches = [r for r in records if r["layer"] == layer]
        if not matches:
            layers[layer] = {"status": "not_run"}
            continue
        r = matches[-1]  # Append order is chronological; never overwrite a receipt.
        status = r.get("status", "not_run")
        reason = []
        if status not in ("passed", "failed", "blocked", "not_run"):
            reason.append("invalid_status")
        if r.get("source_sha256") != current:
            reason.append("source_revision_mismatch")
        if not r.get("observed_at") or not r.get("environment"):
            reason.append("missing_observation_metadata")
        artifacts = r.get("artifacts", [])
        if not isinstance(artifacts, list) or not artifacts:
            reason.append("missing_artifact")
        else:
            for a in artifacts:
                if not isinstance(a, dict):
                    reason.append("invalid_artifact")
                    continue
                p = Path(a.get("path", ""))
                if not p.is_absolute() or not p.is_file():
                    reason.append("artifact_missing_or_not_absolute")
                elif a.get("sha256") != digest(p):
                    reason.append("artifact_changed")
        if layer in ("phone_preview", "effect_detection", "submission", "review"):
            if not package:
                reason.append("package_not_supplied")
            elif r.get("package_sha256") != digest(package):
                reason.append("package_revision_mismatch")
        if layer in ("submission", "review") and not r.get("effect_id"):
            reason.append("missing_effect_id")
        layers[layer] = {"status": "unverified" if reason else status,
                         "recorded_status": status, "reasons": sorted(set(reason))}
    return {"source_sha256": current, "layers": layers, "record_count": len(records),
            "note": "Integrity checks only. Artifact meaning and genuine device execution require human/agent review."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "assets", "size", "snapshot", "evidence", "preflight"))
    parser.add_argument("project", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--project-limit-bytes", type=int)
    args = parser.parse_args()
    try:
        project = args.project.expanduser().resolve(strict=True)
        manifest = json.loads((project / "effect.dyehpj").read_text())
        if not isinstance(manifest, dict) or not (project / "Assets").is_dir():
            raise ValueError("invalid project manifest or missing Assets")
        package = args.package.expanduser().resolve(strict=True) if args.package else None
        if package and not package.is_file():
            raise ValueError("package must be a file")
        if args.project_limit_bytes is not None and args.project_limit_bytes <= 0:
            raise ValueError("size limit must be positive")
        ledger = args.ledger.expanduser().resolve() if args.ledger else project / ".douyin-effect/evidence.json"
        report = {"command": args.command, "project": str(project),
                  "observed_at": datetime.now(timezone.utc).isoformat(),
                  "identity": {k: manifest.get(k) for k in ("name", "version", "projectID")}}
        links = []
        for base, dirs, names in os.walk(project, followlinks=False):
            for name in dirs + names:
                p = Path(base) / name
                if p.is_symlink():
                    links.append(str(p.relative_to(project)))
        report["unsupported_symlinks"] = links
        blocked = bool(links)
        if args.command in ("doctor", "preflight", "assets"):
            report["structure"] = legacy("inspect_project.py", project)
            blocked |= report["structure"]["exit_code"] != 0
        if args.command == "doctor":
            report["runtime"] = legacy("inspect_douyin_runtime.py", project, True)
            blocked |= report["runtime"]["exit_code"] != 0
        if args.command in ("assets", "preflight"):
            report["sequences"] = sequences(project)
        if args.command in ("size", "preflight"):
            report["size"] = inventory(project, args.project_limit_bytes, package)
            blocked |= report["size"]["over_limit"] is True
        if args.command == "snapshot":
            report["source_sha256"] = revision(project)
            report["package_sha256"] = digest(package) if package else None
        if args.command in ("evidence", "preflight"):
            report["evidence"] = evidence(project, ledger, package)
            blocked |= any(report["evidence"]["layers"][k]["status"] != "passed" for k in LAYERS)
        if args.command == "preflight":
            report["gate"] = "incomplete" if blocked else "record_integrity_ready"
            report["manual_checks"] = ["current UI size limits", "icon clarity and upload completion",
                                       "actual tracking/motion evidence", "content safety and rights", "submission authorization"]
            report["note"] = "This command never certifies platform approval or submits an effect."
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"{args.command}: {project}")
            for key, value in report.items():
                if key not in ("command", "project"):
                    print(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        return 1 if blocked else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": "Check paths, JSON schema, permissions, or diagnostic timeout."}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
