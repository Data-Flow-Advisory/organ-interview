# organ-interview

A **pure decision organ**: given the state of a discovery interview, decide the
**next question to ask**. Extracted from
`discovery-engine app/services/claude_service.py::next_question` and the
theme-pack fallback banks — the proven logic, with the monolith wiring left
behind.

```
decide(state, context) -> {output, rationale, self_metric}
```

## Pure, with the Claude call as an IO edge

Generating a question with an LLM is **not** in this organ. Per the connection
standard's *IO organs* section, the Claude call is an **IO edge** fulfilled by
`organ-claude-adapter` (the substrate's effect runner). This organ is the pure
half: it reads the interview state — which may already carry the raw model
output fetched on that edge under `model_output` — and:

- **model output present + parseable** → normalises it into a well-formed
  question (qtype validation, opening-turn forcing, scale-widget validation,
  schema defaults);
- **model output absent / malformed** → serves a deterministic question from
  the theme-pack fallback bank.

Either way the same end-of-interview floor/ceiling rules are applied, so the
verdict is reproducible and testable with no network.

## Ports (the connection standard)

| dir | name | type | notes |
|-----|------|------|-------|
| in  | `state` | `InterviewState` | the whole interview context as one composite wire (history, theme pack, framing, optional `model_output`, caps) |
| out | `next`  | `InterviewQuestion` | the next question + qualifiers (theme, probe type, depth, qtype, options, `should_end`) |

`decide()` reads the `InterviewState` bundle under `state["state"]` and writes
the `InterviewQuestion` under `output["next"]`.

Both types are **proposed** additions to the shared vocabulary
(`vocab/proposed_types.json`); the PR that adds this organ proposes them upstream
in the orchestrator's `types.json` (CONNECTORS.md: *new types reviewed, not minted
freely*). `InterviewQuestion` is reused **verbatim** from `organ-claude-service`'s
proposal so the two interview question-generator organs are **brick-swappable** on
that wire — `organ-interview` simply registers as a second producer of the same
type.

### InterviewState (input bundle)

```jsonc
{
  "history": [["Q1", "A1"], ["Q2", null], ...],   // prior Q/A pairs
  "theme_pack": "ops",                            // which vocabulary drives it
  "fallback_questions": [["Q", "theme", "probe"], ...],  // bank override (optional)
  "job_role": "Operations Manager",
  "business_function": "Logistics",
  "opening_idea": "Better scheduling",
  "skipped_themes": ["risk_safety"],
  "interview_type": "user",            // user|kickoff|review|persona_session
  "model_output": { ... } | null,      // raw JSON the Claude IO edge returned
  "min_questions": 8,                  // end-of-interview floor (default 8)
  "max_questions": 20                  // end-of-interview ceiling (default 20)
}
```

### InterviewQuestion (output)

```json
{
  "question": "Walk me through how the daily rota gets built today.",
  "theme": "process",
  "probe_type": "locate",
  "depth": 2,
  "should_end": false,
  "qtype": "open_text",
  "options": null,
  "allow_other": false,
  "rationale": "...",
  "used_fallback": false
}
```

## Proven rules reabsorbed

- **qtype guard** — unknown widget types downgrade to `open_text`; the first 3
  turns are forced to `open_text`; `name_capture` strips options; `scale_1_5`
  must have exactly 5 options valued `"1".."5"` or it downgrades to `open_text`.
- **Fallback bank** — index `min(len(history), len(bank)-1)` walks the theme-pack
  bank so a fallen-back interview still spans the theme spread; override the bank
  via `fallback_questions` to keep it domain-relevant.
- **End-of-interview** — never `should_end` before `min_questions`; force
  `should_end` at `max_questions`.

## Run it

```bash
echo '{"state":{"state":{"history":[["Q","A"]]}},"context":{}}' | python3 organ.py
# or
ORGAN_INPUT=samples/03_fallback_no_model.json python3 organ.py
```

## Conformance

- `conformance_validate.py` — asserts the `{output, rationale, self_metric}`
  shape with numeric `self_metric.confidence`.
- `conformance_ports.py` — the connection-standard port check (ports.json
  parses; types exist; `decide()` reads `state['state']` and writes
  `output['next']`).
- `test_organ.py` / `test_ports.py` — behavioural + port pins for pytest.

The `conformance` GitHub Action runs all of the above on every push/PR.
