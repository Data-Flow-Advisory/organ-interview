"""Port-manifest conformance, runnable under pytest.

Wraps conformance_ports.check_ports() so the connection-standard port check
(ports.json parses; declared types exist in the vocabulary; decide() reads each
input name under `state` and writes each output name under `output`) is pinned
by the test suite as well as the conformance Action.
"""

import json
import os

from conformance_ports import check_ports

HERE = os.path.dirname(os.path.abspath(__file__))


def test_port_conformance_passes():
    ok, msgs = check_ports()
    assert ok, "\n".join(msgs)


def test_ports_json_shape():
    with open(os.path.join(HERE, "ports.json")) as f:
        ports = json.load(f)
    assert [p["name"] for p in ports["inputs"]] == ["state"]
    assert ports["inputs"][0]["type"] == "InterviewState"
    assert [p["name"] for p in ports["outputs"]] == ["next"]
    assert ports["outputs"][0]["type"] == "InterviewQuestion"


def test_proposed_types_present():
    with open(os.path.join(HERE, "vocab", "proposed_types.json")) as f:
        proposed = json.load(f)["types"]
    assert "InterviewState" in proposed
    assert "InterviewQuestion" in proposed
    # organ-interview is declared a producer of InterviewQuestion (brick-swap
    # parity with organ-claude-service, which proposes the identical schema).
    assert "organ-interview" in proposed["InterviewQuestion"]["produced_by_eg"]


def test_interview_question_schema_matches_sibling():
    """The InterviewQuestion schema must stay byte-identical to the sibling's so
    the two question-generator organs are interchangeable on that wire."""
    with open(os.path.join(HERE, "vocab", "proposed_types.json")) as f:
        schema = json.load(f)["types"]["InterviewQuestion"]["schema"]
    assert schema == {
        "question": "str",
        "theme": "str",
        "probe_type": "str",
        "depth": "int",
        "should_end": "bool",
        "qtype": "str",
        "options": "array|null",
        "allow_other": "bool",
    }
