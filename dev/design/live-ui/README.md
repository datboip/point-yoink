# Live UI screenshots

Real captures of the running app, for review of the actual interface (not
mockups). Rendered headless at 1200x1000 via `dev/render.py`.

- 01_projects_next / 02_import: refreshed at 0.9.111-pre (2026-09-16).
- 03_files_tab and 04_help..08_export: still from 0.9.62-pre. render.py can't
  switch to the Files tab or open the modal dialogs, so these need a live
  capture (or a render.py extension) to refresh.

- 01_projects_next.png - Projects page: list, NEXT bar, 3D preview, scan strip, right-panel actions.
- 02_import.png - Import page (no scanner attached).
- 03_files_tab.png - Files tab: model-file list + the save-folder tree.
- 04_help.png - "How to use" dialog.
- 05_cutbase.png - Remove base (cut-plane) dialog.
- 06_prepare.png - Prepare dialog (before/after, the five cleanup toggles).
- 07_combine.png - Combine: two pick views on top, wide merged view below.
- 08_export.png - Export dialog (version, format, size + mesh check).

Known issue visible in 01: the NEXT bar can pick up a leftover Prepare temp file
(`combined_clean.tmp`) as a scan node - part of the NEXT-logic / version-history rework.
