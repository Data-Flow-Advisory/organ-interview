"""Behavioural pins for organ-interview.

Each test pins a property reabsorbed from
discovery-engine app/services/claude_service.py::next_question — the proven,
pure half of interview-question generation. The Claude call is an IO edge and
lives outside this organ; here we only assert the deterministic normalisation,
fallback, and end-of-interview rules.
"""

import json
import os

from organ import (
    DEFAULT_FALLBACK_BANK,
    DEFAULT_MAX_QUESTIONS,
    DEFAULT_MIN_QUESTIONS,
    OPEN_TEXT_PRELUDE,
    decide,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def _state(iv):
    return {"state": iv}


def _next(iv, context=None):
    return decide(_state(iv), context or {})["output"]["next"]


# --------------------------------------------------------------------------
# Canonical shape.
# --------------------------------------------------------------------------

def test_canonical_shape():
    result = decide(_state({"history": []}), {})
    assert set(result) == {"output", "rationale", "self_metric"}
    assert "next" in result["output"]
    assert isinstance(result["self_metric"]["confidence"], (int, float))
    assert not isinstance(result["self_metric"]["confidence"], bool)


def test_empty_input_is_safe():
    # No state at all -> a fallback opening question, not a crash.
    result = decide({}, {})
    q = result["output"]["next"]
    assert q["used_fallback"] is True
    assert q["question"]


# --------------------------------------------------------------------------
# Model-output normalisation.
# --------------------------------------------------------------------------

def test_model_output_is_normalised():
    q = _next({
        "history": [["q", "a"]] * 4,
        "model_output": {"question": "Why?", "theme": "pain_point", "probe_type": "exception"},
    })
    assert q["question"] == "Why?"
    assert q["used_fallback"] is False
    assert q["qtype"] == "open_text"  # defaulted
    assert q["options"] is None


def test_unknown_qtype_downgrades_to_open_text():
    q = _next({
        "history": [["q", "a"]] * 4,
        "model_output": {"question": "X?", "qtype": "carousel"},
    })
    assert q["qtype"] == "open_text"


def test_first_turns_forced_to_open_text():
    # Even when the model asks for a choice widget, the opening turns are open_text.
    for h in range(OPEN_TEXT_PRELUDE):
        q = _next({
            "history": [["q", "a"]] * h,
            "model_output": {
                "question": "Pick one",
                "qtype": "single_choice",
                "options": [{"label": "A", "value": "a"}],
                "allow_other": True,
            },
        })
        assert q["qtype"] == "open_text", f"turn {h} should be open_text"
        assert q["options"] is None
        assert q["allow_other"] is False


def test_choice_widget_kept_after_prelude():
    q = _next({
        "history": [["q", "a"]] * OPEN_TEXT_PRELUDE,
        "model_output": {
            "question": "Pick one",
            "qtype": "single_choice",
            "options": [{"label": "A", "value": "a"}],
            "allow_other": True,
        },
    })
    assert q["qtype"] == "single_choice"
    assert q["options"] == [{"label": "A", "value": "a"}]
    assert q["allow_other"] is True


def test_name_capture_strips_options():
    q = _next({
        "history": [["q", "a"]] * 5,
        "model_output": {"question": "Who else?", "qtype": "name_capture", "options": [1, 2]},
    })
    assert q["qtype"] == "name_capture"
    assert q["options"] is None
    assert q["allow_other"] is False


def test_valid_scale_1_5_kept():
    opts = [{"label": str(i), "value": str(i)} for i in range(1, 6)]
    q = _next({
        "history": [["q", "a"]] * 5,
        "model_output": {"question": "Rate it", "qtype": "scale_1_5", "options": opts},
    })
    assert q["qtype"] == "scale_1_5"
    assert len(q["options"]) == 5


def test_malformed_scale_1_5_downgrades():
    q = _next({
        "history": [["q", "a"]] * 5,
        "model_output": {
            "question": "Rate it",
            "qtype": "scale_1_5",
            "options": [{"label": "Low", "value": "1"}, {"label": "High", "value": "2"}],
        },
    })
    assert q["qtype"] == "open_text"
    assert q["options"] is None


# --------------------------------------------------------------------------
# Fallback bank.
# --------------------------------------------------------------------------

def test_fallback_when_no_model_output():
    q = _next({"history": [["q", "a"]]})
    assert q["used_fallback"] is True
    # index = min(history_len, len(bank)-1) = 1
    assert q["question"] == DEFAULT_FALLBACK_BANK[1][0]
    assert q["theme"] == DEFAULT_FALLBACK_BANK[1][1]


def test_fallback_when_model_output_has_no_question():
    q = _next({"history": [], "model_output": {"theme": "process"}})
    assert q["used_fallback"] is True
    assert q["question"] == DEFAULT_FALLBACK_BANK[0][0]


def test_fallback_index_walks_the_bank():
    seen = []
    for h in range(len(DEFAULT_FALLBACK_BANK) + 2):
        q = _next({"history": [["q", "a"]] * h})
        seen.append(q["question"])
    # First N walk the bank, then it pins to the last entry.
    assert seen[0] == DEFAULT_FALLBACK_BANK[0][0]
    assert seen[-1] == DEFAULT_FALLBACK_BANK[-1][0]


def test_custom_fallback_bank_used():
    bank = [["Custom A?", "process", "locate"], ["Custom B?", "pain_point", "exception"]]
    q = _next({"history": [], "fallback_questions": bank})
    assert q["question"] == "Custom A?"
    assert q["theme"] == "process"


def test_malformed_custom_bank_falls_back_to_default():
    # Bank entries missing fields -> ignored, default bank used.
    q = _next({"history": [], "fallback_questions": [["only-question"], "nonsense"]})
    assert q["question"] == DEFAULT_FALLBACK_BANK[0][0]


# --------------------------------------------------------------------------
# End-of-interview floor / ceiling.
# --------------------------------------------------------------------------

def test_floor_blocks_premature_end():
    q = _next({
        "history": [["q", "a"]] * 3,
        "min_questions": 8,
        "model_output": {"question": "Done?", "should_end": True},
    })
    assert q["should_end"] is False


def test_ceiling_forces_end():
    q = _next({
        "history": [["q", "a"]] * 20,
        "max_questions": 20,
        "model_output": {"question": "More?", "should_end": False},
    })
    assert q["should_end"] is True


def test_model_end_honoured_between_floor_and_ceiling():
    q = _next({
        "history": [["q", "a"]] * 10,
        "min_questions": 8,
        "max_questions": 20,
        "model_output": {"question": "Wrap?", "should_end": True},
    })
    assert q["should_end"] is True


def test_defaults_used_when_caps_absent():
    # No caps in state -> module defaults apply.
    q_floor = _next({
        "history": [["q", "a"]] * (DEFAULT_MIN_QUESTIONS - 1),
        "model_output": {"question": "x", "should_end": True},
    })
    assert q_floor["should_end"] is False
    q_ceiling = _next({
        "history": [["q", "a"]] * DEFAULT_MAX_QUESTIONS,
        "model_output": {"question": "x", "should_end": False},
    })
    assert q_ceiling["should_end"] is True


# --------------------------------------------------------------------------
# self_metric / rationale.
# --------------------------------------------------------------------------

def test_self_metric_source_and_confidence():
    model = decide(_state({"history": [["q", "a"]] * 4, "model_output": {"question": "x"}}), {})
    assert model["self_metric"]["source"] == "model"
    assert model["self_metric"]["confidence"] == 0.9

    fb = decide(_state({"history": [["q", "a"]]}), {})
    assert fb["self_metric"]["source"] == "fallback"
    assert fb["self_metric"]["confidence"] == 0.5


def test_stage_opening():
    sm = decide(_state({"history": []}), {})["self_metric"]
    assert sm["interview_stage"] == "opening"


# --------------------------------------------------------------------------
# Samples all run and conform.
# --------------------------------------------------------------------------

def test_all_samples_conform():
    samples_dir = os.path.join(HERE, "samples")
    files = [f for f in os.listdir(samples_dir) if f.endswith(".json")]
    assert files, "no samples"
    for fn in files:
        with open(os.path.join(samples_dir, fn)) as fh:
            data = json.load(fh)
        result = decide(data.get("state", {}), data.get("context", {}))
        assert set(result) == {"output", "rationale", "self_metric"}, fn
        assert "next" in result["output"], fn
        q = result["output"]["next"]
        assert q.get("question"), fn
        assert q.get("qtype") in {"open_text", "single_choice", "multi_choice", "scale_1_5", "name_capture"}, fn
        assert isinstance(q.get("should_end"), bool), fn
