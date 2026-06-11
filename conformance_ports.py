#!/usr/bin/env python3
"""Port-manifest conformance checker (the connection standard's port check).

Implements the extra conformance the connection standard asks for
(orchestrator@feat/drift-gate/CONNECTORS.md, section "Conformance gains a
port check"):

  - ports.json parses and has the {inputs: [...], outputs: [...]} shape,
    each port carrying a non-empty `name` and `type`;
  - every declared `type` exists in the shared vocabulary
    (vocab/types.json), or in this repo's locally-proposed additions
    (vocab/proposed_types.json) — the latter reported as amber, not fatal,
    so the repo stays green while the upstream type review lands;
  - decide() actually READS each declared input `name` under `state`
    (static check against organ.py source);
  - decide() actually WRITES each declared output `name` under `output`
    (dynamic check: run decide() on every sample and assert the key is
    present in the returned `output`).

Lives in a committed module (not an inline `python3 -c`) so the workflow
YAML stays free of embedded multi-line Python — same convention as
conformance_validate.py. Importable: `check_ports()` returns
(ok: bool, messages: list[str]) for use from pytest; `main()` prints the
messages and returns a process exit code.
"""

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PORTS_PATH = os.path.join(HERE, "ports.json")
VOCAB_PATH = os.path.join(HERE, "vocab", "types.json")
PROPOSED_PATH = os.path.join(HERE, "vocab", "proposed_types.json")
ORGAN_PATH = os.path.join(HERE, "organ.py")
SAMPLES_DIR = os.path.join(HERE, "samples")


def _load_json(path: str) -> Any:
    with open(path) as f:
        return json.load(f)


def _load_vocabulary() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (official_types, proposed_types) name->def maps.

    Proposed types are optional; a missing file is treated as none proposed.
    """
    official = _load_json(VOCAB_PATH).get("types", {})
    proposed: Dict[str, Any] = {}
    if os.path.exists(PROPOSED_PATH):
        proposed = _load_json(PROPOSED_PATH).get("types", {})
    return official, proposed


def _reads_state_key(source: str, name: str) -> bool:
    """True if organ.py reads `name` under `state` (state.get / state[...])."""
    n = re.escape(name)
    patterns = [
        r"state\s*\.\s*get\(\s*[\"']" + n + r"[\"']",
        r"state\s*\[\s*[\"']" + n + r"[\"']\s*\]",
    ]
    return any(re.search(p, source) for p in patterns)


def check_ports(decide_fn=None) -> Tuple[bool, List[str]]:
    """Run all port checks. Returns (ok, messages).

    decide_fn lets a caller inject the organ's decide(); when None it is
    imported from organ.py so this module works standalone in CI.
    """
    msgs: List[str] = []
    ok = True

    # 1. ports.json parses and has the right shape.
    try:
        ports = _load_json(PORTS_PATH)
    except (OSError, json.JSONDecodeError) as e:
        return False, [f"FAIL: ports.json does not parse: {e}"]

    inputs = ports.get("inputs")
    outputs = ports.get("outputs")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        return False, ["FAIL: ports.json must have list `inputs` and `outputs`"]

    for section, ports_list in (("input", inputs), ("output", outputs)):
        for p in ports_list:
            if not isinstance(p, dict) or not p.get("name") or not p.get("type"):
                ok = False
                msgs.append(f"FAIL: {section} port missing name/type: {p!r}")
    if not ok:
        return ok, msgs
    msgs.append(f"OK   ports.json parses ({len(inputs)} inputs, {len(outputs)} outputs)")

    # 2. every declared type exists in the vocabulary (official or proposed).
    official, proposed = _load_vocabulary()
    for section, ports_list in (("input", inputs), ("output", outputs)):
        for p in ports_list:
            t = p["type"]
            if t in official:
                msgs.append(f"OK   {section} `{p['name']}` -> {t} (official)")
            elif t in proposed:
                msgs.append(
                    f"AMBER {section} `{p['name']}` -> {t} "
                    f"(locally proposed, pending upstream review)"
                )
            else:
                ok = False
                msgs.append(
                    f"FAIL: {section} `{p['name']}` type `{t}` not in vocabulary "
                    f"(vocab/types.json) nor proposed (vocab/proposed_types.json)"
                )

    # 3. decide() reads each declared input name under state.
    with open(ORGAN_PATH) as f:
        source = f.read()
    for p in inputs:
        name = p["name"]
        if _reads_state_key(source, name):
            msgs.append(f"OK   decide() reads state['{name}']")
        else:
            ok = False
            msgs.append(
                f"FAIL: declared input `{name}` is never read under `state` in organ.py"
            )

    # 4. decide() writes each declared output name under output (run samples).
    if decide_fn is None:
        sys.path.insert(0, HERE)
        from organ import decide as decide_fn  # type: ignore

    sample_files = sorted(
        os.path.join(SAMPLES_DIR, f)
        for f in os.listdir(SAMPLES_DIR)
        if f.endswith(".json")
    )
    if not sample_files:
        ok = False
        msgs.append("FAIL: no samples to check declared outputs against")

    declared_outputs = [p["name"] for p in outputs]
    for sf in sample_files:
        data = _load_json(sf)
        result = decide_fn(data.get("state", {}), data.get("context", {}))
        out = result.get("output") or {}
        for name in declared_outputs:
            if isinstance(out, dict) and name in out:
                msgs.append(f"OK   [{os.path.basename(sf)}] output has '{name}'")
            else:
                ok = False
                msgs.append(
                    f"FAIL: [{os.path.basename(sf)}] declared output `{name}` "
                    f"not written under `output`"
                )

    return ok, msgs


def main(argv: Optional[List[str]] = None) -> int:
    ok, msgs = check_ports()
    for m in msgs:
        print(m)
    if ok:
        print("\n✓ Port conformance passed")
        return 0
    print("\n✗ Port conformance FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
