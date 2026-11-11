#!/usr/bin/env python3
"""Check reviewer output against the bundled findings schema before round conversion."""

import argparse
import json
from pathlib import Path
import sys


def check(value, schema, location="$"):
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    matched = {"object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list),
               "string": lambda: isinstance(value, str), "integer": lambda: type(value) is int,
               "null": lambda: value is None}
    if types and not any(matched[t]() for t in types):
        raise ValueError(f"{location}: expected {types}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{location}: invalid enum value")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise ValueError(f"{location}: missing required fields")
        if schema.get("additionalProperties") is False and value.keys() - props.keys():
            raise ValueError(f"{location}: unexpected fields")
        for key in value.keys() & props.keys():
            check(value[key], props[key], f"{location}.{key}")
    if isinstance(value, list):
        for i, item in enumerate(value):
            check(item, schema["items"], f"{location}[{i}]")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw", type=Path)
    ap.add_argument("--schema", type=Path, default=Path(__file__).resolve().parent.parent / "assets" / "findings-schema.json")
    args = ap.parse_args()
    try:
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
        data = json.loads(args.raw.read_text(encoding="utf-8-sig"))
        check(data, schema)
        for item in data["findings"]:
            if not item["path"].strip() or not item["claim"].strip() or "\n" in item["claim"]:
                raise ValueError("Finding needs a path and a one-line claim")
            if item["line"] is not None and item["line"] < 1:
                raise ValueError("Finding line must be positive or null")
    except (OSError, ValueError) as exc:
        print(f"Invalid reviewer output: {exc}", file=sys.stderr)
        return 2
    print("Valid findings contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
