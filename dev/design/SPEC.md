# PointYoink layout spec (target for 0.9.x -> 1.0)

Derived from the reference mock (dev/design/mock.png, when present) and the layout research of
2026-09-11. Dark theme. Default window 1090x1070, must stay usable at 1024x600 with nothing clipped.

## Structure, top to bottom

1. Header (one row, ~56 px)
   - Left: logo + "PointYoink" wordmark.
   - Middle-left: mode tabs, text with an icon, underline on the active one:
     **Import** (active by default) | **Process** (tag "In development") | **Live view** (tag "Planned").
     In our app: Import = the projects screen, Process = Process on PC / editing tools, Live view = the Live mode.
   - Right: ⚙ Settings (and ? / i as small icon buttons).

2. Device bar (~60 px, full width, below the header)
   - Scanner icon, model name ("MIRACO Pro"), a status dot + word ("Connected" green / "Not connected" amber /
     "Not detected" grey), then a divider, then connection controls: **Wi-Fi** (dropdown-style button: opens the
     share flow), **USB** ("USB available" when the cable is in, becomes the connect action), **Refresh**.
   - This replaces the bottom status banner for device state. Activity/progress stays in the bottom bar.

3. Body: three columns
   - Left (~360 px): "On your scanner" title, a search field ("Search projects…"), then the project list.
     Each row: checkbox, thumbnail (~80 px), friendly name (bold; the user's rename or the id), the scanner id
     in muted text, "size · date" line, and a status line ("✓ Imported" / "on PC"). Selected row tinted.
     Footer line under the list: "N projects selected".
   - Centre (flex): project title (bold, ~20 px) with a rename pencil, the id underneath in muted text.
     A row with tabs **3D preview | Files** on the left and view controls on the right (Solid / Wireframe /
     Reset view in the mock; ours: View in 3D, Remove base, Process on PC as an outlined button group).
     The preview area: large shaded render of the mesh on a dark grid background, a hint line
     "Drag to rotate · Scroll to zoom" (ours: "Click View in 3D to rotate").
     Under it: the scan strip, each thumbnail in a rounded frame with a label "Scan 01", "Scan 02"…, the
     selected one outlined in accent; to the right a stats line "Mesh · 1.8M triangles".
   - Right (~320 px): "Import options", always visible:
     radio **Finished models** ("Skip raw frames") / **Full project** ("Includes raw capture data");
     divider; "Also export as" with checkboxes STL, OBJ, GLB and the note "Original PLY files are kept";
     divider; "Destination" folder field with a folder button; checkbox "Open folder when done".
     (Ours adds "Clean up mesh".)

4. Bottom bar (~70 px)
   - Left: "N projects · 86 MB" (selection summary).
   - Right: **Open folder** (secondary), **Import N projects** (the ONLY filled primary button, with a
     small dropdown chevron for "Import and zip" etc.).

## Rules

- One filled accent button in the window: the Import button. Everything else outlined or ghost.
- Tinted chips for status, not solid fills. Body text ~87% white, secondary ~60%.
- Nothing clipped at 1024x600: list, preview area and the options column scroll independently.
- No em dashes in UI text. Plain language, no jargon in labels.
- Keep every widget attribute name that other code uses (see the comment at the top of `_body` in
  pointyoink.py) so behaviour does not change with the look.

## Second reference (dev/design/mock2.png)

Same family, simpler: two columns only. Header = logo, Settings, Help. Device bar = green dot +
"MIRACO Pro connected", a Wi-Fi | USB segmented pair, "Refresh projects" on the right. Left =
"Projects", search field, Select all / Clear, rows with a large (~100 px) thumbnail, bold friendly
name, "date  size", and "Imported" at the right; selected rows tinted with an accent outline.
Right = project title, Preview | Files tabs, a big shaded render on a dark grid, then a row with
"Open 3D viewer" (outlined, external-link icon) and "2 meshes · 4 scans", then "Scans in this
project (3)" with framed thumbnails labelled "Scan 1" + size. Bottom strip = "Import content"
radio Models only / Full project | "Open folder when finished" | "Save to" entry + Browse.
Footer = "2 projects selected · 86 MB" and the single primary "Import 2 projects".

What makes both mocks feel rich: the shaded grey-material mesh render on a grid floor (not the
scanner's flat preview), large thumbnails, generous spacing, one accent. Shaded renders are the
priority visual feature: decimate, light, grid, cache to THUMBS, render in a worker thread.
