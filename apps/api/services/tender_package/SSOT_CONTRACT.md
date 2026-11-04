# Tender-package SSOT v1

This is an isolated module-three contract. It does not modify or import the legacy
extract/generate/assemble pipeline. It never changes ZIP unpack, module-two
material selections, original DOCX documents or application frontend code.

## Inputs and reading entry

- `build_ssot(classified_manifest, extraction_root, module2_final_path, rules=None)`
  accepts the role-labelled manifest and the validated module-two final envelope.
- `read_ssot(path_or_dict_or_model)` returns a validated Pydantic `SSOT` object.
  This is the sole intended reading entry for future package-generation adapters.
  It needs only the serialized SSOT, not the original files.
- `write_ssot(model, new_path)` writes JSON without overwriting existing outputs.
- `python -m api.services.tender_package.ssot --manifest ... --root ... --module2 ... --output ...`
  runs independently. Set `PYTHONPATH=apps;packages` in PowerShell first.

No downstream generator is connected in this module. The single-input boundary
is an API contract for the subsequent module, not a claim that legacy generators
have already been migrated. `response_table_ref` is an input-only logical key,
not a generated response document.

## Fields

`schema_version`: tender-package-ssot/1.
`rules_version`, `rules_sha256`, `rules_snapshot`: reproducible aggregation rules.
`fields`: tender_number, project_name, procurement_method, subject_type,
procurement_mode, delivery_period, price_ceiling, selected_lots.
`documents`: complete ordered body paragraphs and table rows, logical and storage
paths, SHA-256, role, local contract subtype, explicit paragraph/table/row locators.
`inventory`: every unpacked entry, including archive containers and signatures.
`lots`: explicit catalogue/lot/package records with lot_code, lot_name,
package_code, package_name, province, project_unit, unit, quantity and row sources.
Unknown attributes are represented as missing facts, never inferred from a
province/company name list or a filename. A catalogue row is not a selected lot.
`requirements`: general/special qualifications, rejection conditions, delivery,
pricing, agreement/general/special contract text and effective clause candidates.
Exact normalized duplicates merge evidence. Effective general/special terms with
matching clause numbers preserve all differing text and apply source authority;
this is a review aid, not a semantic/legal decision that two terms contradict.
`pricing_rows`: explicit header/value records with applicability and material_ref;
only full IDs matching module two can be linked. Other lots' limits remain
unassigned, not applied to this package. There is no guessed global price cap.
`materials`: one entry per module-two ID; the entire original mapping is included
unchanged, with document IDs, parameter-point evidence, response-template headers
and a unique logical response table reference. Manual corrected material values
remain in the imported module-two selected_sources; raw technical rows remain raw.
`module2_mapping`, `module2_payload_sha256`: the original final payload and digest.
`strategy`: goods/works/services only when the type fact is resolved, else pending.
`pending`, `conflicts`, `warnings`, `manual_history`: explicit review queues.

## Fact and evidence contract

A fact contains value, candidates, status, needs_confirmation and reason. Status
is resolved/missing/conflict/unranked/manual. A candidate records value, authority,
sources and basis. Evidence records document_id, locator and exact source quote;
locators and quotes are validated against the embedded document corpus.

All authority rankings and field/column aliases live in ssot_rules.json. No
specific company, project, lot, material ID or product model is configured there.
Lower numbers indicate higher authority. If values differ, the preferred value
may be present but all candidates are preserved and confirmation remains required.
If no ranked source exists, value stays unresolved. Templates, blank fields and
references to other attachments are not treated as actual populated values.
A mixed contract uses the nearest agreement/general/special section per block,
rather than assigning one authority to its entire file.

Scalar numeric-looking quantities/prices remain source strings; units, tax labels
and scopes are retained verbatim. No currency conversion, total summation or
cross-lot limit inference occurs. Technical name/ID recognition is not rerun:
module-two selections are embedded as-is; DOCX bodies are read once to capture
additional technical/contract clauses not present in the module-two name mapping.

## Human supplementation

`confirm_field(ssot, path, value, actor=..., basis=..., evidence_refs=[...])`
accepts `fields.<name>`, `lots.<lot_key>.<field>` and
`requirements.<requirement_key>`. It retains prior candidates and records actual
UTC confirmation time. Evidence must reference an embedded source block. For
selected_lots, supply a list of known lot keys; package scope is not guessed.
`add_requirement(...)` adds an explicitly reviewed missing requirement in a
supported category, with the same actor/basis/evidence requirements. No frontend
is connected. Truly external supplementary documents must be added to the source
manifest first, so a decision cannot carry an invented source locator.

## Completeness and limits

The source corpus preserves body paragraphs and direct tables even when no
semantic rule matches. That demonstrates source retention, not guaranteed
understanding of every sentence. DOCX, TXT and CSV body readers are supported;
unsupported formats remain in inventory with readable warnings and need parsed
input. Headers/footers, image OCR and spreadsheet adapters are not introduced in
this module. No LLM or external network service is required.

The supplied real package includes a multi-lot procurement catalogue and limits
for IDs outside the ten confirmed materials. Selected lot scope, unlabelled
province/project-unit fields, definitive delivery period and applicable cap can
remain pending. The SSOT is schema-validated but is not evidence that all
business facts have been confirmed or that a bid is ready for submission.

## Verification of supplied real package

Validated output: tmp/real-package-ssot-v1-validated.json.
Review/coverage report: tmp/real-package-ssot-v1-validation.json.

The output retains all 29 inventory entries and 15 DOCX document bodies (5,740
paragraph/table-row blocks), 10 unchanged module-two material mappings, 769
technical parameter rows, and 30 response-template header references. There are
17 lot catalogue records and 48 pricing rows. The identified procurement mode is
agreement inventory and the subject strategy is goods.

15 same-clause-number wording differences remain visible, and 88 review paths
remain pending, including catalogue/package applicability, unspecified province
and project-unit fields, delivery period and applicable price ceiling. These are
not missing material names: module two still has 9 composed and 1 manual names.
The source corpus and explicit unresolved markers are delivered; complete
business confirmation or guaranteed semantic extraction of every clause is not
claimed. No module-four generation or formal frontend integration was performed.

All 29 source hashes were verified. The embedded module-two final payload is
exactly equal to the independent final mapping, whose file SHA-256 remains
f09240a6507e0dfc95116ff1886edfcc51f780db6bcbdf7cc3e0b6e4c3f8fe01.
ZIP unpack SHA-256 remains
AB9A3F2EE0166B781F1E3A29D69B40CD55A33851C83AA62FB04FEF40FDA337B0.

Regression after the supplementation-state fix: 479 passed, including the prior
465 tests and 14 SSOT tests. Multi-structure fixtures are synthetic; real-package
validation is limited to the user-supplied package, not undisclosed examples from
other procurement organizations. The only warning is the existing intentional
duplicate-name ZIP test fixture.
