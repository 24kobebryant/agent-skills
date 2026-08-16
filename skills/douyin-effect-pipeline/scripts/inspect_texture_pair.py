#!/usr/bin/env python3
"""Validate two PNG frames intended for state switching in Douyin AR."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


def png_info(path: Path) -> tuple[int, int, int, bool]:
    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a valid PNG")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    has_alpha = color_type in (4, 6) or b"tRNS" in data
    return width, height, bit_depth, has_alpha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path, help="first/closed PNG")
    parser.add_argument("second", type=Path, help="second/open PNG")
    parser.add_argument("--max-size", type=int, default=2048)
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []
    infos = []
    for path in (args.first, args.second):
        try:
            info = png_info(path.expanduser().resolve())
            infos.append(info)
            width, height, depth, alpha = info
            print(f"{path}: {width}x{height}, bit_depth={depth}, alpha={str(alpha).lower()}")
            if not alpha:
                errors.append(f"{path} has no alpha channel; a baked checkerboard is not transparency")
            if max(width, height) > args.max_size:
                warnings.append(f"{path} exceeds recommended max dimension {args.max_size}")
        except (OSError, ValueError) as exc:
            errors.append(f"{path}: {exc}")

    if len(infos) == 2 and infos[0][:2] != infos[1][:2]:
        errors.append(
            "frame dimensions differ; normalize canvas size and anchor before import "
            f"({infos[0][0]}x{infos[0][1]} vs {infos[1][0]}x{infos[1][1]})"
        )

    for item in warnings:
        print(f"WARN: {item}")
    for item in errors:
        print(f"ERROR: {item}")
    print(f"Summary: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
