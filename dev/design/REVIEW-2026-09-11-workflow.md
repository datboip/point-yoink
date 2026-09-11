# Workflow review, 2026-09-11 (forwarded by the user, written with ChatGPT)

Kept here as a reference so it does not have to be re-sent. Our response and
what was adopted is at the bottom.

## Take 1: guided scan-to-model workflow

PointYoink needs a guided scan-to-model workflow. "Edit" implies tools you
don't have yet, and "Clean up" doesn't explain what someone gets.

Path: Import -> Build if needed -> Prepare -> Export

Have the app figure out where to start:

| What arrived | Next step |
|---|---|
| Raw scan data | Build a 3D model |
| Point cloud | Create a mesh, when supported |
| Finished scanner mesh | Review model, then prepare or export |
| Several separate scans | Review individually; align and merge only when that feature exists |

Building each scan and combining different views are separate steps. Don't
imply "Build" produces one complete part from several captures.

- Import: "Finished models" or "Full project, includes data for building on
  PC." Explain what's available before they choose.
- Build: only appears when needed. Keep scanner originals and PC-built models
  clearly identified.
- Prepare: replace the vague cleanup button with named actions: Remove
  floating pieces, Smooth surface, Fill small holes, Reduce triangle count.
  Show a before/after preview and let people keep or discard the result.
- Export: choose the model version, format and destination together. For STL
  show dimensions and mesh checks. An STL export alone doesn't mean printable.

Each scan should have one obvious next button: Build model, Review model, or
Export. Advanced settings behind "Options". Rename "Process" to "Prepare" and
remove "Edit" for now.

## Take 2: what other apps do, and forum requests

Strongest direction: "get my scans into Linux, turn them into a usable model,
and keep my originals safe." Better control over each step, not more
automatic smoothing.

| App | Useful approach | Borrow |
|---|---|---|
| Artec Studio | Separates unwanted-data removal, alignment, registration, surface creation, model editing | Make each operation understandable; inspect alignment before building the final surface |
| Meshroom | Stores steps and intermediate results; a changed setting only invalidates later steps | Keep reusable results so changing export or cleanup settings doesn't restart everything |
| Revo Scan | Repeatable one-click processing, but users ask for precise local controls | Guided default with individual tools when something looks wrong |

Forum requests that fit:

| Asked for | Could build | Priority |
|---|---|---|
| Native Linux tools, even a minimal export/CLI workflow | Reliable Linux import, processing, optional command-line export | Core |
| Keep alignment between sessions without merging | Save alignment, lock scans, reopen where you left off | High |
| Remove bad regions while viewing overlapping scans | Per-scan colours, visibility toggles, select/delete in the combine workspace | High |
| Orient a model using a known flat surface | Pick a surface -> Make horizontal; recenter pivot after cropping | High |
| Easier lasso selection | Click-to-place polygon selection, brush selection, undo | High |
| Smooth only a troublesome area | Selection-based smoothing with protected areas | Later |
| Inspect and exclude bad capture frames | Frame timeline with reversible exclusion before rebuilding | Later |

Two clear paths:
- Finished model: Import -> Inspect -> Optional repairs -> Export.
- Several scans of one object: Import -> Prepare individual scans -> Align ->
  Review overlap -> Combine into one surface -> Inspect -> Export.

Scans of the same physical piece get combined. Two different pieces belong in
an assembly view where they stay separate: "Combine scans" and "Position parts
together" are different actions.

Roadmap in priority order:
1. Reliable project handling: version/import bugs, preserve originals, recover
   interrupted work, distinguish received / saved / exported, record which
   source and settings produced each version.
2. A small editing toolbox: lasso/brush selection, crop plane, base removal,
   undo/redo, show/hide scans, measurements, orientation.
3. Proper scan combining: automatic alignment with manual matching-point
   fallback, saved transforms, coloured overlap preview, exclude poor regions.
4. An export check: units, dimensions, disconnected pieces, open boundaries,
   invalid geometry; choose which holes to fill; Export STL and Open in
   slicer, without calling every mesh "print-ready".
5. Faster repeat work: draft previews, background job queue, cancel between
   stages, presets, batch export, reuse intermediate results.

Live view after those four.

## Our response (2026-09-11)

Adopted now: rename Process -> Prepare; the four named actions instead of
"Clean up" (each maps to one process.py flag: --isolation-rate, --smooth-times,
--fill-holes, --simplify-pct); one next-step button per scan; Edit button on
the Import column reworded. Already done before this review: originals kept,
versions labelled scanner / built here / cleaned, keep-and-add on re-import,
memory-capped processing, recover interrupted WiFi transfers.

Agreed but later, in this order: export check (dimensions, pieces, open
edges, units) with the version picker; per-version provenance record; the
editing toolbox (we have a crop plane and base removal already); align and
combine; presets and job queue. Before/after preview is cheap with the two
cached renders and comes with the named actions.

Not adopted: "Create a mesh from a point cloud" as a separate stage (our
build goes straight from raw data to a mesh).
