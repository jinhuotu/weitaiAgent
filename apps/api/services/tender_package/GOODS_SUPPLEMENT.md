# Module four reviewed data and visual review

All new capabilities remain inside tender_package. No unpack, module-two final
mapping, module-three SSOT implementation/output, engineering entry or application
frontend is changed. The inputs are overlays referencing a specific SSOT digest.

## Manual guarantee and quote input

`goods_inputs.create_input_session(ssot)` prepares every technical target with its
solidified_id, document_id:locator key, source quote and original cells; it also
lists original price-template references. It creates no guarantee or quotation.

`confirm_offer(session, code, target, guarantee, actor=..., basis=..., status=None)`
adds a timestamped event. `compile_inputs` uses the latest explicit manual event
for each target while retaining all earlier events. A source-less or empty value
is rejected. The renderer still independently checks determinable numeric/exact
requirements; conflicting review states remain pending.

`confirm_packages(session, ssot, packages, actor=..., basis=...)` requires known
SSOT lot keys, explicitly supplied actual package codes and complete nonduplicate
material assignment. No package number is inferred from an archive name.

`confirm_price(session, package_key=..., document_id=..., table=..., row=...,
column=..., amount=..., currency=..., unit=..., actor=..., basis=...)` records an
actual approved quote, not a tender ceiling. Amounts must be finite/nonnegative.
`compile_inputs` withholds prices not bound to confirmed packages and creates a
review warning. Only blank recognized price/tax columns can be filled. Nonempty
source cells remain unchanged and produce warnings. The original SSOT is never
rewritten to make an offer appear to have been present in the tender.

`compile_inputs(session, ssot)` produces kwargs accepted by generate_goods,
including input_provenance with the entire event history. Different SSOT hashes
are rejected. Standard JSON can be saved/reloaded for reproducible review.
CLI: --reviewed-inputs <session.json>.

Real empty session: tmp/goods-reviewed-inputs-empty.json. It contains 769 targets
and four original quotation-template references. It deliberately has no invented
input values. This is a data/API entry, not a formal module-five frontend.

## Real supplier-product parameters

`read_product_source(path, document_id, solidified_ids=[...], binding_actor=...,
binding_basis=...)` reads actual DOCX product tables with parameter-name,
actual/measured/nominal-value and unit columns. Tender requirement-value columns
are not treated as offered values. Other source formats currently require a
reviewed structured/manual input rather than silent extraction.

`match_product_offers(ssot, products)` rechecks file existence/SHA-256 and rereads
parameter values from real bytes, not caller-edited parameter JSON. A source
whose hash belongs to a tender document is refused as supplier guarantee evidence.
Product-to-material bindings need named confirmation. Parameter labels and units
must match exactly; cross-file differences produce review candidates, not a guess.
Each matched value retains file ID, absolute source path, SHA-256, table/row,
original cells, binding confirmer and basis. Complex response/deviation judgment
still needs explicit review. CLI: --product-sources <product-records.json>.
Manual confirmed values take precedence over automatic matches in the CLI merge.

## Actual library file selection

The read-only resolve_business_library adapter now returns candidate_media with
real document IDs, paths and SHA-256. `select_library_files(snapshot, selections,
actor=..., basis=...)` only accepts those candidates, rechecks their hashes and
keeps timestamped selection history. An empty selection cannot assert that no
files exist. The selected snapshot can be saved and passed via --library.
Selection history is included in the generated business report. Actual missing
files and unselected/ambiguous files continue to be treated differently.

Current candidate snapshot: tmp/goods-library-candidates.json. It contains two
matched candidate associations requiring actual selection. No files have been
chosen on the user's behalf in this work.

## Renderer contract

`goods_render.Renderer`: available() and render(document_path, new_output_dir)
returning every page's PNG paths. Adapters are registered in renderer_from_config:
- libreoffice: configurable executable, timeout and rasterization scale;
- command: argument list with {input}/{output} placeholders; shell execution is
  disabled, allowing an existing headless renderer/document-service client.

CLI: --render-config <json>; config has kind and options. A missing configuration
probes LibreOffice without installing or downloading anything. Renderer
unavailability and exceptions do not abort other generated content.

`render_package` tracks each DOCX hash, PNG paths/hashes and per-document state.
An unavailable renderer reports every document as not_rendered and labels the
package "仅结构检查通过、视觉未验收". Successful render remains
rendered_pending_review, never automatically accepted. External renderer adapters
must return all actual pages; renderer/backend completeness is part of their
contract, not inferred from filenames.

`review_page(report, page_path, actor=..., basis=..., checks=...)` records human
review of layout, pagination, alignment, blank_regions and original_format. Every
page must pass every check. Changes to any document or reviewed PNG invalidate
acceptance. Synthetic image fixtures in tests are interface tests, not real visual
inspection of the tender. No formal frontend or remote rendering service is
started/configured by this module.

## Real sample status

The real package still has no supplied actual guarantees, prices, approved
packages or confirmed product-document binding. No test values are applied to it.
The generated inputs remain empty, 769 guarantee rows stay pending, and the
unassigned package directory remains. Existing SSOT pending/conflicts and critical
qualification/cap issues stay in warnings outside the Word body.

No usable renderer was found in this environment, so the real output remains
structure-only / visual not accepted. Module-four implementation/test completion
is distinct from confirmation of the real bid's business values or visual quality.
No module-five integration is performed.
