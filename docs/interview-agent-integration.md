# Interview Agent integration baseline

`feat/interview-agent` was reconciled into the contract-v2 `main` architecture rather than
copying its standalone FastAPI/React layout over the lead-owned monorepo.

## Runtime boundary now owned by main

- Orchestrator supplies a bounded assignment containing the current case, verified source
  registry, mode, handoff name and output schema.
- `Interview` is loaded through the configured `app.agents:interview_adapter` slot and receives
  only the shared model gateway. It cannot dispatch specialists or perform Odoo business writes.
- The host validates the strict five-question guide or cited summary before Odoo persists an
  artifact. Orchestrator remains responsible for routing and overall run state.
- Existing evidence rules remain authoritative: approved criteria, exact source references,
  no candidate score/rank/decision, and no claim that a prepared guide is participation.

## Legacy branch mapping

The v1 branch's `assignment/context/result/output/open_items/next_action` handoff intent maps to
the v2 adapter assignment plus host-controlled run events and Odoo artifact references. Its
session-state adapter is not copied because the v2 host must read session state through signed,
company-scoped Odoo workflow operations rather than a local SQLite callback.

## Explicit remaining work

This merge establishes ancestry and the safe adapter boundary; it does **not** claim that the two
interactive modes are ported. Quick AI screening with OpenAI Realtime and the AssemblyAI
human-interview transcription/translation assistant remain follow-up implementation tasks. They
must plug into the Interview adapter/session contracts without bypassing consent, source freshness,
speaker attribution, rubric/evidence grounding or Odoo authority.
