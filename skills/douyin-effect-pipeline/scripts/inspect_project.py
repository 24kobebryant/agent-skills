#!/usr/bin/env python3
"""Read-only structural audit for a Douyin AR project."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


SOURCE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".ts",
    ".scene",
    ".ssubgraph",
    ".mp3",
    ".wav",
    ".m4a",
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_info(path: Path) -> tuple[int, int, bool]:
    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("invalid PNG signature")
    length = struct.unpack(">I", data[8:12])[0]
    if data[12:16] != b"IHDR" or length != 13:
        raise ValueError("missing PNG IHDR")
    width, height, _bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    has_alpha = color_type in (4, 6) or b"tRNS" in data
    return width, height, has_alpha


def collect_guids(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        guid_value = value.get("guid")
        if isinstance(guid_value, dict):
            candidate = guid_value.get("a")
            if isinstance(candidate, str) and candidate:
                found.append(candidate)
        for nested in value.values():
            found.extend(collect_guids(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(collect_guids(nested))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Douyin AR project directory")
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []

    print(f"Project: {project}")
    if not project.is_dir():
        print("ERROR: project directory does not exist")
        return 2

    manifest_path = project / "effect.dyehpj"
    assets_dir = project / "Assets"
    if not manifest_path.is_file():
        errors.append("missing effect.dyehpj")
    if not assets_dir.is_dir():
        errors.append("missing Assets directory")

    if manifest_path.is_file():
        try:
            manifest = read_json(manifest_path)
            safe = {
                "name": manifest.get("name", ""),
                "version": manifest.get("version", ""),
                "toolName": manifest.get("toolName", ""),
                "projectID": manifest.get("projectID", ""),
            }
            print("Manifest: " + json.dumps(safe, ensure_ascii=False))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid effect.dyehpj: {exc}")

    if not assets_dir.is_dir():
        for item in errors:
            print(f"ERROR: {item}")
        return 1

    source_files = sorted(
        path
        for path in assets_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES
    )
    ts_files = [path for path in source_files if path.suffix.lower() == ".ts"]
    scenes = [path for path in source_files if path.suffix.lower() == ".scene"]
    print(f"Assets: {len(source_files)} source files, {len(ts_files)} TypeScript, {len(scenes)} scenes")

    if not scenes:
        warnings.append("no .scene source found under Assets")

    for scene in scenes:
        try:
            scene_text = scene.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"cannot inspect scene {scene.relative_to(project)}: {exc}")
            continue

        entity_count = len(re.findall(r"^  - __class: Entity$", scene_text, re.MULTILINE))
        image_count = len(re.findall(r"__class: ImageRenderer", scene_text))
        screen_transform_count = len(
            re.findall(r"^--- !ScreenTransform", scene_text, re.MULTILINE)
        )
        scene_extra = Path(str(scene) + ".extra")
        metadata_count = 0
        if scene_extra.is_file():
            try:
                metadata = read_json(scene_extra)
                metadata_count = len(metadata) if isinstance(metadata, list) else 0
            except (OSError, json.JSONDecodeError):
                pass

        print(
            f"Scene {scene.relative_to(project)}: entities={entity_count}, "
            f"image_renderers={image_count}, screen_transforms={screen_transform_count}, "
            f"metadata_records={metadata_count}"
        )

        suspicious_numeric_guids = re.findall(
            r"guid: \{a: (?:[6-9][0-9]0{12,}[0-9]+), b: (?:[6-9][0-9]0{12,}[0-9]+)\}",
            scene_text,
        )
        suspicious_string_guids = re.findall(
            r'"a": "[a-f0-9]{1,4}0{4,}-0{4}-4[0-9a-f]{3}-8[0-9a-f]{3}-0{8,}[0-9a-f]*"',
            scene_extra.read_text(encoding="utf-8") if scene_extra.is_file() else "",
        )
        if image_count and (len(suspicious_numeric_guids) >= 3 or len(suspicious_string_guids) >= 3):
            warnings.append(
                f"scene {scene.relative_to(project)} contains repeated patterned GUIDs; "
                "treat it as possibly hand-authored and require a single-image visual smoke test"
            )

    missing_sidecars = [path for path in source_files if not Path(str(path) + ".extra").is_file()]
    for path in missing_sidecars:
        warnings.append(f"missing sidecar: {path.relative_to(project)}.extra")

    guid_to_paths: dict[str, set[Path]] = defaultdict(set)
    unreadable_extra: list[Path] = []
    for extra in sorted(assets_dir.rglob("*.extra")):
        try:
            for guid in collect_guids(read_json(extra)):
                guid_to_paths[guid].add(extra)
        except (OSError, json.JSONDecodeError):
            unreadable_extra.append(extra)

    for extra in unreadable_extra:
        warnings.append(f"unreadable JSON sidecar: {extra.relative_to(project)}")
    for guid, paths in sorted(guid_to_paths.items()):
        if len(paths) > 1:
            joined = ", ".join(str(path.relative_to(project)) for path in sorted(paths))
            errors.append(f"duplicate GUID {guid}: {joined}")

    hash_groups: dict[str, list[Path]] = defaultdict(list)
    for path in source_files:
        try:
            if path.stat().st_size <= 50 * 1024 * 1024:
                hash_groups[sha256(path)].append(path)
        except OSError as exc:
            warnings.append(f"cannot hash {path.relative_to(project)}: {exc}")
    for digest, paths in sorted(hash_groups.items()):
        if len(paths) > 1:
            joined = ", ".join(str(path.relative_to(project)) for path in paths)
            warnings.append(f"identical source files {digest[:12]}: {joined}")

    icon_dir = project / "Icon"
    for icon_name in ("femaleIcon.png", "maleIcon.png"):
        icon = icon_dir / icon_name
        if not icon.is_file():
            warnings.append(f"missing current icon file: Icon/{icon_name}")
            continue
        try:
            width, height, has_alpha = png_info(icon)
            print(f"Icon/{icon_name}: {width}x{height}, alpha={str(has_alpha).lower()}")
            if width != height:
                warnings.append(f"Icon/{icon_name} is not square")
        except (OSError, ValueError) as exc:
            errors.append(f"invalid Icon/{icon_name}: {exc}")

    pngs = [path for path in source_files if path.suffix.lower() == ".png"]
    invalid_pngs = []
    alpha_count = 0
    for png in pngs:
        try:
            _width, _height, has_alpha = png_info(png)
            alpha_count += int(has_alpha)
        except (OSError, ValueError) as exc:
            invalid_pngs.append((png, exc))
    print(f"PNG assets: {len(pngs)}, with alpha: {alpha_count}")
    for png, exc in invalid_pngs:
        errors.append(f"invalid PNG {png.relative_to(project)}: {exc}")

    lint_result = project / "Library" / "CompiledScripts" / "Out" / "lint_result.json"
    compiled_js = project / "Library" / "CompiledScripts" / "Out"
    print(
        "Generated evidence: "
        f"lint_result={'present' if lint_result.is_file() else 'absent'}, "
        f"compiled_out={'present' if compiled_js.is_dir() else 'absent'}"
    )
    print("Note: generated evidence is not proof that the current source compiles; verify editor logs.")

    for item in warnings:
        print(f"WARN: {item}")
    for item in errors:
        print(f"ERROR: {item}")

    print(f"Summary: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
