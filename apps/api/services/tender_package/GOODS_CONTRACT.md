# Module four goods response adapter

This directory's goods_adapter.py is an independent opt-in adapter. It does not
change or call the legacy single-DOCX engineering generation entry. It reuses
existing tender pdf_format._set_run_text, placeholders._insert_slot_media,
placeholders._draw_box, match.attachment_requests/attachment_catalog_item,
library_kb.resolve_attachments and expiry_check.collect_expiry_warnings.
No existing generator, unpack, module-two mapping, SSOT implementation or formal
frontend file is edited by this module.

## Inputs

`generate_goods(ssot_source, template_root, new_output_directory, ...)` accepts a
validated SSOT and existing templates whose hashes must match SSOT. Templates are
read only to preserve the original OOXML and fill preselected locations; facts
come from the SSOT, not a second document-recognition pass.

Optional inputs:
- offers: {solidified_id: {document_id + ':' + locator:
  {guarantee, actor, basis, status?}}}. Basic plain numeric inequalities and exact
  text matches are checked deterministically. Complex requirements require a
  named manual response/deviation decision. A contradiction between manual and
  calculable status stays pending. No guarantee is inferred from a requirement.
- packages: [{key, lot_key, package_code, material_ids, price_templates?}].
  All material IDs must be covered exactly once. Output keys are restricted to
  safe ASCII identifiers. Missing scope goes to scope_pending, not all catalogue
  lots. A price template reference is document_id + ':' + one-based table index.
- library: actual selected slots/media/documents from resolve_business_library.
  That coroutine reads the current library without ensure/seed/commit calls.
  Ambiguous multiple-file choices need selected_ids. Unknown/unselected/unreadable
  materials do not get mislabeled as absent. Only absent selected file sets get
  labelled frames; known rendering failures do not get missing-material frames.
- reviewed_business_texts: text/actor/basis for actual approved declaration text.
  No promise or qualification statement is invented when the list is empty.
- business_forms: explicit package_key/document_id/table_fragment?/changes/
  actor/basis plans for original business forms. The shared patch adapter only
  fills blank designated cells. Original nonempty text is retained with a warning.
- price_fills: explicit package/template/cell bindings with actor/basis and an
  ssot_pricing_row reference. Unconfirmed or out-of-scope price rows cannot be
  used. Without actual offers, original price forms are exported without prices.
- rules: a copy of goods_rules.json with deployment-specific port formats and
  capacity limits. Null capacity limits mean unknown, not platform compliance.
  Optional platform_channel_limits supplies business/technical/price byte limits.

## Output and invariants

Per package: technical/, business/, price/ directories. Technical output is one
DOCX per ID. All SSOT parameter rows receive a corresponding result record. Blank
guarantee and status/remarks cells can be filled; all other source cells, titles,
rows, columns, styles, relationships and resources remain unchanged. A verification
copy restores edited cells and compares canonical original OOXML. Non-document
ZIP members are verified byte-for-byte. Original files are never overwritten.

Price forms are exact original table fragments plus their adjacent original
caption; tables are not reconstructed. All original rows and columns remain.
Multiple candidates are exported with a template-selection warning. Selecting a
form is different from confirming its actual prices.

Business output without original form bindings is a supplemental attachments
volume, not a claim that the complete formal business bid has been authored.
Known scans use the original shared writer. Failed/partial insertions are reported.
Only successfully embedded file identities participate in read-only expiry checks.
Expiry and SSOT warnings stay in result.json and never enter DOCX body content.

result.json contains source SSOT digest, rules, package mapping, technical rows,
business outcomes and sources, original price form references, file hashes/sizes,
all SSOT pending/conflict paths, deviations and warnings. Critical qualification,
rejection and cap issues are sorted first. Unreviewed output is always marked
ready_for_submission=false. Oversize files/ports/packages get explicit compression
or permitted-splitting advice; documents are not silently degraded or split.

## Real-sample verification and remaining limitations

Latest draft: tmp/goods-module4-validated-draft/result.json.
Machine verification: tmp/goods-module4-validated-draft/validation.json.
10 technical files, 769 parameter rows, all marked pending because no actual
supplier guarantees were supplied. One business supplement and four original
price forms were exported. The library read succeeded; two material categories
were ambiguous and were not automatically selected. No actual files were chosen
for this run. The other absent entries use labelled attachment positions.

Scope remains unassigned: the 17 SSOT catalogue lots were not treated as the
selected package. Prices, guarantees, formal business statements, actual material
selection and ECP capacity rules require human/source inputs. These are drafts,
not submission-ready business/technical/price bids, and module-four full business
acceptance is not claimed.

Original 29 source file hashes are unchanged. All technical table edits passed
OOXML structure verification; the four price tables are identical to source
canonical XML. Rendering was attempted with the documents skill renderer, but
LibreOffice soffice.exe is unavailable. No PNG visual QA was possible. Do not
claim visual layout verification or distribute these as final DOCX deliverables
before the required render-and-inspect gate is completed. No manual install or
external network dependency was added to the application.

## Running

CLI supports --ssot, --root, --output, --offers, --packages, --library, --rules.
The richer original-form/price-fill adapters are available through generate_goods.
The CLI prints a short result and stores the detailed report locally. A library
snapshot can be loaded with load_library_snapshot; snapshots preserve actual
file IDs and expiry metadata without including live ORM objects in outputs.

Tests cover response/deviation/pending, exact row coverage, original structure,
actual-image vs missing/ambiguous materials, expiry isolation, failed rendering,
price-table preservation, original business form binding, capacity warnings,
critical ordering, traversal/omission rejection and old SSOT pending propagation.
