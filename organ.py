#!/usr/bin/env python3
"""
Interview Question Organ — pure decision logic extracted from discovery-engine.

A pure, stdlib-only decider that reads {state, context} JSON on stdin and
writes {output, rationale, self_metric} on stdout.

It decides the NEXT interview question given the prior answers and the theme
the interview is probing. The proven logic is reabsorbed from
``discovery-engine app/services/claude_service.py::next_question`` and the
theme-pack fallback banks — but the monolith wiring is left behind.

The Claude call is NOT here. Generating a question with an LLM is an *IO edge*
(fulfilled by ``organ-claude-adapter`` / the substrate's effect runner), per the
connection standard's "IO organs" section. This organ is the pure half: given the
interview state — which may carry the raw model output already fetched on that
edge — it validates/normalises that output into a well-formed question, applies
the deterministic question-type and end-of-interview rules, and falls back to the
theme-pack question bank when the model output is missing or unusable. So:

  * model output present + parseable  -> normalise it into the next question
  * model output absent / malformed   -> deterministic theme-pack fallback

Either way the same end-of-interview floor/ceiling rules apply, so the organ's
verdict is reproducible and testable with no network.

Contract
--------
INPUT  (stdin JSON): {"state": {...}, "context": {...}}

  state = {
    "state": {                       # the InterviewState bundle (port `state`)
      "history": [["Q1","A1"], ["Q2", null], ...],   # prior Q/A pairs
      "theme_pack": "ops",           # which theme vocabulary is driving this
      "themes": ["process", ...],    # theme vocabulary (optional override)
      "fallback_questions": [["Q","theme","probe"], ...],  # bank (optional override)
      "job_role": "Operations Manager",
      "business_function": "Logistics",
      "opening_idea": "Better scheduling",
      "skipped_themes": ["risk_safety"],
      "interview_type": "user",      # user|kickoff|review|persona_session
      "model_output": {              # raw JSON the Claude IO edge returned (optional)
        "question": "...", "theme": "...", "probe_type": "...",
        "qtype": "...", "options": [...], ...
      },
      "min_questions": 8,            # end-of-interview floor (optional)
      "max_questions": 20            # end-of-interview ceiling (optional)
    }
  }
  context = {}                       # reserved; no required keys

OUTPUT (stdout JSON): {
  "output": {
    "next": {                        # the InterviewQuestion (port `next`)
      "question": "...", "theme": "process", "probe_type": "locate",
      "depth": 2, "should_end": false, "qtype": "open_text",
      "options": null, "allow_other": false,
      "rationale": "...", "used_fallback": false
    }
  },
  "rationale": "human-readable explanation of the decision",
  "self_metric": {"confidence": 0.9, ...}
}
"""

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Proven constants reabsorbed from discovery-engine.
# --------------------------------------------------------------------------

# The known answer-widget types. Anything else is downgraded to open_text so we
# never emit a question the surface can't render. (claude_service.py qtype guard)
VALID_QTYPES = frozenset(
    {"open_text", "single_choice", "multi_choice", "scale_1_5", "name_capture"}
)

# End-of-interview floor/ceiling defaults (claude_service.py:
# INTERVIEW_MIN_QUESTIONS / INTERVIEW_MAX_QUESTIONS).
DEFAULT_MIN_QUESTIONS = 8
DEFAULT_MAX_QUESTIONS = 20

# The first N turns are forced to open_text: too early to know who else to
# interview (name_capture) or to offer meaningful choices.
OPEN_TEXT_PRELUDE = 3

# Default theme-pack fallback bank — the proven ops/base default
# (theme_packs/base.py::ThemePack.fallback_questions). Used when the LLM is
# unreachable so the interview still progresses across a wide theme spread.
# Each entry is (question, theme, probe_type). Callers may override the bank via
# state["fallback_questions"] to make a fallen-back interview domain-relevant.
DEFAULT_FALLBACK_BANK: Tuple[Tuple[str, str, str], ...] = (
    ("Walk me through a typical day — what do you do first, and which tool or system do you open?", "process", "locate"),
    ("Where does the information you rely on actually live — a spreadsheet, a system, a person?", "data_location", "locate"),
    ("What's the single most frustrating part of this process?", "pain_point", "exception"),
    ("How much time does this task take across your team each week?", "time_spent", "quantify"),
    ("If this were fixed tomorrow, what would the commercial impact be?", "business_impact", "impact"),
    ("What's the biggest risk or compliance concern in this area?", "risk_safety", "locate"),
    ("Where does the most cost come from in this process?", "labour_cost", "quantify"),
    ("Which handoff between teams causes the most delay?", "process", "handoff"),
    ("What information do you wish you had in real time?", "data_location", "locate"),
    ("When things change unexpectedly, how do you adapt and what can't you see that you wish you could?", "risk_safety", "exception"),
)


# --------------------------------------------------------------------------
# Helpers (all pure).
# --------------------------------------------------------------------------

def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_bank(iv: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """Return the fallback question bank: an interview-supplied override if it is
    a well-formed list of (question, theme, probe) triples, else the default."""
    bank = iv.get("fallback_questions")
    if isinstance(bank, list) and bank:
        cleaned: List[Tuple[str, str, str]] = []
        for entry in bank:
            if isinstance(entry, (list, tuple)) and len(entry) >= 3 and str(entry[0]).strip():
                cleaned.append((str(entry[0]), str(entry[1]), str(entry[2])))
        if cleaned:
            return cleaned
    return list(DEFAULT_FALLBACK_BANK)


def _fallback_question(history_len: int, bank: List[Tuple[str, str, str]]) -> Dict[str, Any]:
    """Pick a fallback question by position in the bank.

    Mirrors claude_service._fallback_question: index = min(len(history),
    len(bank)-1) so successive turns walk the bank and a fallen-back interview
    still spans the theme spread.
    """
    idx = min(history_len, len(bank) - 1)
    question, theme, probe = bank[idx]
    return {
        "question": question,
        "theme": theme,
        "probe_type": probe,
        "depth": 1,
        "rationale": "Fallback question — model output was unavailable.",
        "should_end": history_len >= len(bank),
        "qtype": "open_text",
        "options": None,
        "allow_other": False,
        "used_fallback": True,
    }


def _normalise_model_output(raw: Dict[str, Any], history_len: int) -> Dict[str, Any]:
    """Coerce a raw model-output dict into a well-formed question.

    Faithful re-extraction of the schema-guard / qtype-coercion block in
    claude_service.next_question — the deterministic, pure half that runs after
    the LLM returns.
    """
    data: Dict[str, Any] = dict(raw)

    # Schema guards / defaults.
    data.setdefault("theme", "process")
    data.setdefault("probe_type", "locate")
    data.setdefault("rationale", "")
    data.setdefault("depth", _as_int(data.get("last_answer_depth"), 0))
    data.setdefault("should_end", False)
    data["used_fallback"] = False

    # qtype: unknown -> open_text so we never render a widget we can't handle.
    qtype = data.get("qtype") or "open_text"
    if qtype not in VALID_QTYPES:
        qtype = "open_text"
    data["qtype"] = qtype

    # Force open_text for the opening turns (belt-and-braces — the prompt also
    # asks for this, but a flaky response must not skip the narrative opening).
    if history_len < OPEN_TEXT_PRELUDE:
        data["qtype"] = "open_text"
        data["options"] = None
        data["allow_other"] = False
    elif data["qtype"] in ("open_text", "name_capture"):
        data["options"] = None
        data["allow_other"] = False
    else:
        data.setdefault("options", [])
        data.setdefault("allow_other", False)
        # scale_1_5 MUST have exactly 5 options valued "1".."5"; otherwise
        # downgrade to open_text rather than render something broken.
        if data["qtype"] == "scale_1_5":
            opts = data.get("options") or []
            valid = len(opts) == 5 and all(
                isinstance(o, dict) and str(o.get("value")) == str(i)
                for i, o in enumerate(opts, start=1)
            )
            if not valid:
                data["qtype"] = "open_text"
                data["options"] = None
                data["allow_other"] = False

    # Normalise the depth to an int.
    data["depth"] = _as_int(data.get("depth"), 0)
    # should_end is a bool.
    data["should_end"] = bool(data.get("should_end"))
    return data


def _apply_end_rules(
    question: Dict[str, Any], history_len: int, min_q: int, max_q: int
) -> Dict[str, Any]:
    """Apply the end-of-interview floor and ceiling (pure)."""
    # Never end before the minimum cap regardless of what the model says.
    if history_len < min_q:
        question["should_end"] = False
    # Force-end at the hard cap.
    if history_len >= max_q:
        question["should_end"] = True
    return question


# --------------------------------------------------------------------------
# The organ.
# --------------------------------------------------------------------------

def decide(state: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Decide the next interview question. Pure: no IO, no network.

    Reads the InterviewState bundle under ``state['state']`` and writes the
    InterviewQuestion under ``output['next']``.
    """
    context = context or {}
    iv = state.get("state")
    if not isinstance(iv, dict):
        iv = {}

    history = iv.get("history")
    if not isinstance(history, list):
        history = []
    history_len = len(history)

    min_q = _as_int(iv.get("min_questions"), DEFAULT_MIN_QUESTIONS)
    max_q = _as_int(iv.get("max_questions"), DEFAULT_MAX_QUESTIONS)

    model_output = iv.get("model_output")
    bank = _resolve_bank(iv)

    if isinstance(model_output, dict) and str(model_output.get("question", "")).strip():
        question = _normalise_model_output(model_output, history_len)
        source = "model"
    else:
        question = _fallback_question(history_len, bank)
        source = "fallback"

    question = _apply_end_rules(question, history_len, min_q, max_q)

    # --- rationale + self_metric -----------------------------------------
    if history_len == 0:
        stage = "opening"
    elif question["should_end"]:
        stage = "closing"
    elif history_len < min_q:
        stage = "early"
    elif history_len < max_q:
        stage = "mid"
    else:
        stage = "closing"

    if source == "model":
        rationale = (
            f"Normalised model output into a {question['qtype']} question on theme "
            f"'{question['theme']}' (turn {history_len + 1}, stage={stage})."
        )
        confidence = 0.9
    else:
        rationale = (
            f"Model output unavailable; served fallback question {min(history_len, len(bank) - 1) + 1}"
            f"/{len(bank)} from the theme-pack bank on theme '{question['theme']}' "
            f"(turn {history_len + 1}, stage={stage})."
        )
        confidence = 0.5

    if question["should_end"]:
        if history_len >= max_q:
            rationale += f" Force-ending at the hard cap ({max_q})."
        else:
            rationale += " Model signalled end-of-interview past the floor."

    self_metric = {
        "confidence": confidence,
        "source": source,
        "history_length": history_len,
        "interview_stage": stage,
        "should_end": question["should_end"],
        "used_fallback": question["used_fallback"],
        "min_questions": min_q,
        "max_questions": max_q,
    }

    return {
        "output": {"next": question},
        "rationale": rationale,
        "self_metric": self_metric,
    }


def main(argv: Optional[List[str]] = None) -> int:
    # Input resolution order:
    #   1. $ORGAN_INPUT env var (path to a JSON file — conformance.yml convention)
    #   2. stdin (JSON)
    import os

    input_path = os.environ.get("ORGAN_INPUT")
    if input_path:
        with open(input_path) as f:
            payload = json.load(f)
    else:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}

    result = decide(payload.get("state", {}), payload.get("context", {}))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
