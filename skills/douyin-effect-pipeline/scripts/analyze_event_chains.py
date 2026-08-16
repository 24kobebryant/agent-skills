#!/usr/bin/env python3
"""Heuristic static analysis for Douyin AR event-chain conflicts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk(nested)


def setting_values(value: Any) -> list[str]:
    result: list[str] = []
    for node in walk(value):
        candidate = node.get("value")
        if isinstance(candidate, str):
            result.append(candidate)
    return result


def object_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    target_keys = {"entityid", "entity_id", "objectid", "object_id", "targetid", "target_id"}
    for node in walk(value):
        if node.get("key") == "entity" and isinstance(node.get("value"), dict):
            candidate = node["value"].get("value")
            if isinstance(candidate, (str, int)):
                refs.add(str(candidate))
        for key, candidate in node.items():
            if key.lower() in target_keys and isinstance(candidate, (str, int)):
                refs.add(str(candidate))
    return refs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    path = args.project.expanduser().resolve() / "EventChains" / "eventchains.json"
    if not path.is_file():
        print("Event chains: not found")
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: invalid {path}: {exc}")
        return 1

    chains = data.get("eventChainsManager", {}).get("stateChainList", [])
    warnings: list[str] = []
    refs_to_chains: dict[str, list[str]] = defaultdict(list)
    mouth_open_found = False
    print(f"Event chains: {len(chains)}")
    for index, chain in enumerate(chains):
        name = str(chain.get("name") or f"chain[{index}]")
        condition = chain.get("conditionGroup", {})
        condition_names = {
            str(node.get("name")) for node in walk(condition) if isinstance(node.get("name"), str)
        }
        values = set(setting_values(condition))
        refs = object_refs(chain)
        for ref in refs:
            refs_to_chains[ref].append(name)
        mouth_open_found |= "Mouth Open" in values
        disabled = sum(1 for node in walk(condition) if node.get("disable") is True)
        if disabled:
            warnings.append(
                f"{name}: contains {disabled} disabled condition node(s); disable is not logical negation"
            )
        print(f"- {name}: conditions={sorted(condition_names | values)}, referenced_targets={len(refs)}")

    for ref, names in refs_to_chains.items():
        unique = sorted(set(names))
        if len(unique) > 1:
            warnings.append(f"target {ref} is controlled by multiple chains: {', '.join(unique)}")

    all_values = set(setting_values(chains))
    if mouth_open_found and "Neutral" in all_values:
        warnings.append(
            "Mouth Open and Neutral conditions coexist; they may both be true and race on visibility. "
            "Prefer one mouth-value state machine with hysteresis."
        )
    if mouth_open_found and not {"Mouth Close", "Mouth Closed"}.intersection(all_values):
        warnings.append(
            "mouth-open trigger found without an explicit mouth-close threshold; "
            "verify closed -> open -> closed sequence"
        )

    for item in warnings:
        print(f"WARN: {item}")
    print(f"Summary: 0 error(s), {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
