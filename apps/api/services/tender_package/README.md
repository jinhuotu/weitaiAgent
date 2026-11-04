# Tender package preprocessing

This directory is isolated: no router or generator imports these modules.

- `unpack.py`: historical ZIP entry, unchanged by classification work.
- `classify.py`: `classify(manifest, root, overrides=...)` returns an independent
  manifest copy with role, evidence, candidates, ordering and ID mappings.
  Human overrides are keyed by storage_path and require role/reason/actor;
  optional material overrides explicitly resolve uncertain material names.
- `formats.py`: opt-in extended entry with a shared bounded extraction loop.
- `readers.py`: ZIP reader and optional locally installed rarfile reader.

Known gaps: 7z is explicitly rejected with a conversion instruction. RAR requires
an available local decoder and has not been verified against a real RAR archive.
Do not advertise either format as production-ready. Legacy unpack remains ZIP-only.

DOCX and text content are read offline. Other document types require a supplied
content_reader or manual correction. Filename/heading matches are heuristic;
primary_candidate is only a stable ordering suggestion, never source authority.
No material identity is inferred from component names or product parameter values.

Tests:
python -m pytest apps/api/services/tender_package/test_classify.py tests/tenders tests/test_document_expiry.py

Real sample verification outputs are under tmp/real-package-*.json and excluded
from source fixtures: these contain source metadata and must not be published.

## Material-name evidence

`materials.py` uses explicit name fields in paragraphs or table cells (priority 0),
then material-specific opening specification titles (priority 1). Every candidate
records an OOXML paragraph/table/row/cell locator. A component list, product model
or generic cover is never converted to a procurement material name.

Classification results retain `material_recognition` per file and add
`material_pending_confirmation` / `material_conflicts` queues. Human material
corrections use existing role overrides with material/actor/reason and preserve
raw evidence separately. Cross-document disagreements for the same ID require
confirmation even when a value has higher local authority.

The supplied real ZIP contains 10 technical specification documents with generic
covers and component lists. Current evidence yields 10 IDs, 10 material names
pending, zero explicit name conflicts. This is not a completed material mapping;
authoritative name-bearing source or documented human correction is needed.

## Material completion and isolated review UI

`completion.py` parses XLSX/CSV/TSV mappings, infers columns from labels and full
ID cells, and requires explicit zero-based column indexes on ambiguity. Multiple
Excel sheets require a sheet selection. Legacy XLS must be saved as XLSX/CSV.
Input bytes are bounded; invalid encodings, empty names, duplicate/conflicting IDs
and IDs absent from the package are surfaced. Names are never built from models
or technical parameters.

`complete_materials()` returns a copy with the original mappings, a source ledger,
per-ID sources, selected name/type, conflicts and pending queues. Selection order
is mapping > manual > extracted. A preferred value does not hide disagreement.
`manual_confirmation()` records actor, UTC time and basis. `retire_source()` marks
an incorrect imported/manual record inactive with correction history, keeping its
original content. Reimported/corrected sources do not erase prior entries.

Launch the independent review tool from the repository root in PowerShell:

    $env:PYTHONPATH='apps;packages'
    .venv/Scripts/python -m api.services.tender_package.review --input tmp/real-package-completion-review.json --output tmp/real-package-completion-saved.json

Open the localhost URL printed by the tool. Upload a mapping file, optionally
specify columns/sheet/encoding, or fill each ID's name, confirmer and basis.
Expand the per-ID sources to inspect conflicts. Incorrect external sources can
be retired with a named correction and reason. Each successful change saves a
separate JSON atomically. To resume, pass the saved JSON as --input and a different
--output path. Original recognition is not overwritten. No application service is
started/restarted and no existing frontend/router/generation logic is changed.

The real package still needs authoritative names: the additional supplied folder
contains the same 10 ZIP hashes, not a mapping table. Its known broad category is
kept separately in the review input and never substituted for a formal name.
Synthetic tests demonstrate completion of all 10 IDs via mapping; those test
names must not be applied to the real package.

## Deterministic composed names

`composed.py` exposes `compose_rows`, `parse_model`, `compose_document`, and
`compose_materials(classified, root, rules=None)`. This is an explicit opt-in
preprocessing call; classification, ZIP unpack, existing frontend and tender
builders are unchanged. Load `tmp/real-package-composed.json` in the independent
review tool to review composed results with the existing manual/import actions.

`cable_codes.json` is a versioned, replaceable token grammar, not a lookup table
of complete models. References are included in the rules. Its supported scope is
common extruded-insulation power-cable codes; unconfigured prefixes, families or
suffixes are rejected. No claim is made to decode every standard cable family.
Add verified token definitions before enabling another family.

TABLE0 is zero-indexed; evidence row and column numbers are one-indexed, include
the header row, and retain the original field label/value. The requirement-value
column is located by its header; bidder-guarantee values are never substituted.
Only a unique explicit voltage grade or consistent parenthesized kV grade on
insulation parameters qualifies. Test/withstand voltages cannot supply a grade.
No U0/U pair is invented from a lone system voltage (e.g. 10kV remains 10kV).
Every generated part records its source rows; rule version, rule digest, parsed
tokens, source file digest and all raw TABLE0 rows are retained in composition_history.

Priority is mapping > manual > composed > extracted. Failed composition attempts
remain available for review. Authoritative mapping/manual decisions can resolve
composition issues without deleting those issues or prior composition names.

The real package yields 9 composed names and 1 conductor conflict, not the proposed
8/2 expectation. ID 9985-500108211-00001 has an L conductor token (aluminum) in
TABLE0 row 2 and explicit copper in row 3. ID 9985-500138466-00001 is internally
consistent: non-magnetic armor and polyethylene outer sheath match its 63 suffix.
This discrepancy is reported explicitly rather than manufacturing a second conflict.

## Module-two final export contract (SSOT preparation only)

`closure.py` defines `SCHEMA_VERSION = tender-package-material-map/1`.
It does not aggregate SSOT or import any generator/router/frontend module.

1. `confirm_material(classified, code, conductor=..., name=..., actor=..., basis=...)`
   records an explicit human decision with actual UTC confirmation time and
   source document identity. It never supplies a default human identity, name or
   conductor. It retains all original recognition and composition history.
2. `build_final_mapping(classified, root, expected_ids=...)` recomputes selection
   with mapping > manual > composed > extracted, rejects pending/missing items,
   checks expected IDs and per-ID name/source uniqueness, verifies source file
   containment/existence/SHA-256, and requires traceable selected sources.
3. `write_final_mapping(artifact, destination)` writes a new UTF-8 JSON file;
   existing outputs cannot be overwritten.
4. `read_final_mapping(path_or_dict)` is the downstream reading entry. It checks
   schema/status, payload digest, counts, uniqueness and completion, returning an
   independent copy of the payload. Hashes provide integrity, not authenticity.

Envelope fields: payload, payload_sha256. Payload fields: schema_version, status
(final), created_at (UTC), id_count, entries, recognition_snapshot,
recognition_sha256. Each entry contains solidified_id, material_name, source_type,
category, files (logical_path/storage_path/sha256), selected_sources,
composition_history, all_sources, historical_conflict, status (confirmed).
Source records retain table/row/column evidence for composed values, or human
actor/time/basis/confirmed_parameters for manual values. Original recognition is
included unchanged in the snapshot; historical_conflict may remain true even
when an explicit authoritative decision has resolved the pending status.

A pending item cannot be finalized from a request alone. The subsequent explicit
human decision and real final export are recorded below.

## Page-based conflict confirmation

The independent review page now requests a conductor choice for IDs with a
composition history. The choice starts empty; options come from the configured
conductor vocabulary. No specific ID or conductor is selected by application code.
Raw model/table conflicts are displayed before the form. Final name, confirmer
and basis remain required; actual confirmation time is recorded on save.

Launch with the composed dataset so its parameter evidence is available:

    $env:PYTHONPATH='apps;packages'
    .venv/Scripts/python -m api.services.tender_package.review --input tmp/real-package-composed.json --output tmp/real-package-human-reviewed.json

The same page and POST /manual handler work for future IDs with similar conflicts.
The handler validates explicit human values through closure.confirm_material and
retains raw recognition/composition history. Previously entered wrong sources can
be retired through the existing source-correction controls before resubmission.
This does not amend the original technical specification, its conductor field,
the existing application UI, or any document-generation path.


## Real package closure record

On 2026-11-03, the user explicitly confirmed copper conductor and the corresponding
YJV22 model, overriding the original conflicting YJLV22 field. The confirmation is
attributed to the user in this conversation, not to an inferred personal identity.
The final name uses those confirmed fields and the original 10kV / 3×400mm² values.
The manual source retains the verbatim decision, actual confirmation timestamp,
original document hashes, original conflicting rows and composition history.

Final outputs (new files; prior page-session output is unchanged):
- tmp/real-package-manual-confirmed.json: editable review snapshot.
- tmp/real-package-module2-final.json: versioned SSOT-consumable final envelope.

Validated 10 unique IDs: 9 composed, 1 manual, zero pending. The historical conflict
remains present and resolved by authority. All 29 original file hashes and ZIP
unpack hash remain unchanged. Full related regression: 465 passed.
The existing page process retains its own input/session; it does not automatically
switch to these new files. To review the confirmed state, supply the editable
review snapshot as the standalone tool's input and choose a new output path.
No SSOT aggregation, goods response generation or application frontend integration
was implemented by this closure step.
