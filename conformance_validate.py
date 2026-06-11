#!/usr/bin/env python3
"""Conformance shape validator.

Reads an organ's stdout (JSON) from stdin and asserts the canonical shape:
  {output, rationale, self_metric} with a numeric self_metric.confidence.

Exits 0 if the output conforms, 1 (with a message) otherwise. Used by
.github/workflows/conformance.yml so the workflow YAML carries no embedded
multi-line Python (which would break the `run: |` block scalar and make
GitHub reject the workflow with a startup failure).
"""

import json
import sys


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "<stdin>"
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"FAIL [{label}]: organ output is not valid JSON: {e}")
        print(raw[:2000])
        return 1

    required = {"output", "rationale", "self_metric"}
    missing = required - set(data)
    if missing:
        print(f"FAIL [{label}]: missing top-level keys: {sorted(missing)}")
        return 1

    sm = data.get("self_metric")
    if not isinstance(sm, dict):
        print(f"FAIL [{label}]: self_metric is not an object")
        return 1
    if "confidence" not in sm:
        print(f"FAIL [{label}]: self_metric missing 'confidence'")
        return 1
    if not isinstance(sm["confidence"], (int, float)) or isinstance(sm["confidence"], bool):
        print(f"FAIL [{label}]: confidence must be numeric, got {type(sm['confidence']).__name__}")
        return 1

    print(f"OK   [{label}]: conforms (confidence={sm['confidence']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
