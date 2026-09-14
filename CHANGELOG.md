# Changelog

All notable changes to PointYoink. Versions before 1.0.0 are pre-release
builds; 1.0.0 will be the first public GitHub release.

## 0.9.30 (dev, 2026-09-13)
- Fixed a real freeze in Captures: every screenshot thumbnail was read over the
  scanner's slow MTP link one at a time, on the UI thread, so a device with a
  lot of screenshots and recordings could lock the window for minutes. Cells
  now appear at once and thumbnails load in the background, one by one.

## 0.9.29 (dev, 2026-09-13)
- **Live view: a distance-quality strip**, recreated from the scanner's own
  screen (Too Near / Excellent / Good / Far / Too Far), computed live from
  the depth stream. Works for the MIRACO in PC mode and the RANGE.
- Device screenshots of the scanner's live-scanning screen and its Scan
  Settings saved for reference.

## 0.9.28 (dev, 2026-09-13)
- Fixed: the status bar said "RANGE live" even when connected to the MIRACO in
  PC mode.

## 0.9.27 (dev, 2026-09-13)
- Fixed a real deadlock behind the frozen splash: the first import of
  trimesh/shapely could be started by two threads at once (the main thread's
  preload and a thumbnail-render thread spawned the moment the project list
  arrived), and one could hang forever finalising a Tk object from the wrong
  thread while holding the import lock the other needed. The import now runs
  before any other thread, or any Tk font, exists.

## 0.9.26 (dev, 2026-09-13)
- The splash appears solid at once; the heavy imports run after it has painted
  instead of stalling its fade-in.

## 0.9.25 (dev, 2026-09-13)
- Live view: IR views shown with a soft stretch so the lit surfaces and the
  dim room are both visible.

## 0.9.24 (dev, 2026-09-13)
- **Live view from the MIRACO over USB.** Put the MIRACO in PC mode and Live
  view shows its depth, both IR cameras and the colour camera, the same panel
  the RANGE uses. Nothing from Revopoint needed. The WiFi position feed is
  still there as a separate source.

## 0.9.23 (dev, 2026-09-13)
- The window comes in invisible, paints completely, then cross-fades with the
  splash; no more watching it build itself around the splash.
- Test instances are never run while the app is open.

## 0.9.22 (dev, 2026-09-13)
- The splash gates properly: the window appears only once the checks are done
  and the first project list is rendered, never piece by piece.

## 0.9.21 (dev, 2026-09-13)
- The main window never appears before the loader reaches 100%: the project
  listing and everything after it wait for the splash to close.

## 0.9.20 (dev, 2026-09-13)
- The splash opens centred on where the main window will appear, so both are
  on the same monitor.

## 0.9.19 (dev, 2026-09-13)
- Startup dialogs (a pending WiFi transfer, the first-run panel) wait until the
  splash has closed, so the main window never appears half-built behind it.
- Drift fix defaults from the measurements: neighbour refinement only when it
  moves a fragment under 2 mm; loop closures are opt-in (they slid a rim-shaped
  scan 40 mm off in the test).

## 0.9.18 (dev, 2026-09-13)
- Fixed the splash deadlock: the heavy libraries are now imported once on the
  main thread before any worker starts. A first import from a worker thread
  could finalise a Tk font on that thread and lock the app on the splash.

## 0.9.17 (dev, 2026-09-13)
- Startup checks run off the UI thread: a slow probe could freeze the splash
  with a blank window behind it. A stack dump on SIGUSR1 for diagnosing a
  freeze.

## 0.9.16 (dev, 2026-09-13)
- Test and render instances never touch the scanner (POINTYOINK_NO_DEVICE=1):
  two apps on one MTP mount froze both.

## 0.9.15 (dev, 2026-09-13)
- Scans are called by the name the device shows (its number, or the name you
  gave it there); "scan 2 of 3" is a hint, not the name.

## 0.9.14 (dev, 2026-09-13)
- Version chips: tooltips say that a click only picks the version and that the
  ✕ asks before deleting; rounded ends stay rounded.

## 0.9.13 (dev, 2026-09-13)
- Versions are named for what they are: the scanner's model, PC build (from raw
  data), prepared copy. The panel and the Compare window say what they are for.

## 0.9.12 (dev, 2026-09-13)
- Scan names given on the scanner are read from the project file and used as
  the default label (pre-filled at import); your own name still wins.
- Startup: the first-run panel waits until the splash is gone; nothing drags the
  main window onto the screen half-built any more.

## 0.9.11 (dev, 2026-09-13)
- **Drift fix for raw scans**: a scan the scanner never fused has only its live
  tracking poses, which wander over a long pass. Build now registers the
  frames first (short fragments, ICP between neighbours and every overlapping
  pair, all poses optimised together) and writes a pose table the way the
  scanner does. Off in Settings if you prefer. Progress shows on the scan.
- Closing the app is instant.

## 0.9.10 (dev, 2026-09-13)
- **Build quality**: per-frame edge and grazing filter, 3-voxel band, 3 views per
  voxel. Our surface now sits within 0.3 mm of One-tap Edit at the 95th
  percentile (was 8.7 mm). Fast-mode scans (400x300 frames) build; the
  calibration is scaled per scan.
- **Remove base in the app**: table direction from the floor grid, auto-detect,
  or any number of clicked spots (least-squares plane); up is the side facing
  the scanner; "No table in this scan" marks the step done.
- Fixed: a hidden 3D view could run its upload inside another view's GL context
  and wreck it (garbage triangles in the cut view on first open). Hidden views
  now wait until they are on screen.
- Import: optional scan names (front, back, left side) with the scanner's
  preview per scan and a pop-out that builds a quick draft you can turn; after
  an import the app jumps to Projects with the project selected; progress line
  inside the bottom bar; WiFi card keeps trying its thumbnail, stats 2x2; all
  dialogs titled with the app name; review window sized for its buttons.
- NEXT bar lays itself out by width; Captures is USB only; empty states fill
  wide windows.

## 0.9.9 (pre-release, testing; source on the dev branch, no package)
- **Import and Projects are separate pages.** Import is the scanner: what is on
  it and the import options. Projects is this PC: the 3D view and scan strip in
  the middle, and a panel on the right with the selected scan's versions and
  actions plus the whole-project actions. The app opens on Projects when no
  scanner is attached.
- **NEXT bar** under the project title says what to do now, with one button
  and the trail Build → Cut base → Combine → Prepare → Export.
- **Remove base per scan** remembers the plane you placed; combining drops
  everything below each scan's plane while fusing, so the table never gets
  fused in. Cut base is its own step before Combine.
- **Combine window** remembers your point pairs: reopen a lined-up scan to see
  its dots and overlay, add or undo points, or start over. Busy bar with the
  stage and seconds; fine adjustment takes seconds, not minutes.
- **Compare versions**: two linked 3D views, any scan or version in each.
- What the scanner already did is visible: raw only / partly scanner-edited /
  scanner-edited badges per project, and per scan.
- 3D views rotate freely with no stops. Prepare dialog shows live linked
  before/after views and can remove the base.
- **Remove base inside the app**: the scan in the GPU view, grey stays and
  red goes, one slider along the table, Flip, Apply. Starts just above the
  flattest surface. The matplotlib window remains only for the software view.
- One ≡ menu replaces Settings, ? and i in the header. Help rewritten for the
  two pages and the five steps; "How this works" panel on first open.
- A project that is both on the scanner and on this PC keeps its PC badges.
- **Prepare page** (was Process): the clean-up button is four named actions
  in one dialog: Remove floating pieces, Smooth surface, Fill small holes,
  Reduce triangle count, each with the scanner's default. It runs on a copy,
  shows before and after, and you Keep or Discard. Every scan card has one
  obvious next step (Build model, Prepare, or Export).
- **Export dialog**: version, format (STL, OBJ, GLB, PLY), folder and file
  name together, with the model's size in mm, triangle count, pieces, open
  edges and whether the surface is closed.
- **Combine scans**: scanned each side separately? Line each scan up to a base
  scan by clicking three to five matching spots on both (or Auto when they
  overlap a lot), check the orange overlay, keep it (saved with the project),
  then build one model from all the scans' raw frames in one go.
- **Build models with the scanner's own registration.** Process on PC now
  reads the globally registered poses the scanner stores with each scan
  (cache/global_register_pose.pose) instead of the per-frame odometry. After
  a Resume Scan the second pass used to sit tens of millimetres off and left
  ghost surfaces. Measured on three scans against the scanner's One-tap Edit:
  median distance 0.17 to 0.23 mm (was 0.5 to 0.8), and only 6 to 15% of the
  scanner's surface is more than 1 mm from ours (was 26 to 45%).

## 0.9.8 (pre-release, testing)
- **Process page**: pick a project, see each scan as a card with its versions
  (from the scanner, built here, cleaned), tick which one counts, Build model
  and Clean up per scan with progress on the card, delete a version or the
  whole project (to the trash), detail levels for building.
- Clean up uses the scanner's own knobs with its defaults (drop pieces under
  15% of the biggest, smooth 3 times, keep all triangles, hole filling off).
  Set them on the Process page; they are remembered.
- The Import column's clean-up checkbox is gone; an Edit button opens the
  Process page instead.
- WiFi safety: every transfer writes files fresh, a request size cap, Models
  only is refused when nothing would be saved, received data is kept when a
  save fails or is cancelled, and a project already on this PC asks for a name
  and whether to keep-and-add or replace.
- The scanner's Fusion and Mesh panels are written down as text in
  dev/design/device/SCANNER-EDIT-OPTIONS.md with the screenshots.
- README states the measured Process on PC accuracy; dev/compare.py is
  memory-capped; the version string shows -pre on testing builds.

## 0.9.7 (pre-release, testing)
- First public pre-release of the new PointYoink: see 0.9.x below. Marked
  unstable on GitHub; 0.7.0 stays the latest release until 1.0.0.
- Loading overlay on the preview; View in 3D button retired; short clean-up line.

## 0.9.6 (local)
- **GPU 3D view**: the preview draws the full model on the graphics card (smooth,
  full detail, drag to rotate); the software view stays as the fallback and can
  be forced in Settings. Wireframe draws a lighter copy so edges are visible.
- Empty states with an illustration and a button instead of blank panels
  (Captures, empty project list, empty preview, Live view).
- Plain words everywhere: "3D model" and "raw scan data" instead of mesh and
  frames. The preview finds models built on this PC and refreshes when
  Process on PC finishes; the list re-scans after any import.
- Settings: 3D view (graphics card / software), build models on GPU or CPU.
- WiFi: the speed graph works on raw-data transfers; the picker no longer
  freezes when a transfer ends; New code releases the port. Captures explains
  that screenshots are USB only. Tooltips no longer flash.

## 0.9.5 (local)
- New window layout, built to the reference concept: mode tabs in the header
  (Import, Captures, Process, Live view), a device bar with the connection
  state and the WiFi / USB / Refresh buttons, a searchable project list with
  friendly names, the project in the centre (title, 3D preview / Files, Solid /
  Wireframe, View in 3D, the scan strip with "Scan 01" tiles and mesh stats),
  import options always visible on the right, and one "Import" button.
- **Interactive 3D in the window**: drag to rotate, scroll to zoom, right-drag
  to pan, double-click to reset, Solid or Wireframe. Software rendered (no GPU
  needed); a cached shaded render shows instantly while the live view loads.
- Review fixes: clean-up writes `_clean.ply` and keeps the original, runs in a
  memory-capped process; ZIP entries from nested imports get unique names and
  conversion failures are counted; import and viewer workers always finish;
  "imported" needs a real file; private thumbnail cache; log copy hides your
  home path; USB connect releases GNOME's grab on the scanner; tooltips no
  longer flash.

## 0.9.4 (local)
- New arrangement: Projects / Captures / Live are modes in the header; WiFi and
  USB sit next to Settings; all status lives in the bottom bar. In Projects the
  list is on the left, Preview / Files in the centre, and a side panel on the
  right chosen from an icon rail: project (thumbnail, chips, actions with short
  explanations), edit, import options, and a folder browser. Click the lit icon
  to fold the panel away. Import selected is the only filled button.
- Empty list explains what to do; status badges are tinted chips; long project
  ids wrap; rename moved into the project panel.
- dev/render.py renders the app off-screen (Xvfb) for design work; dev/design
  holds the layout spec and reference mock.

## 0.9.3 (local)
- Layout rearranged: a project bar above the tabs shows the selected project
  (name, date, scans, meshes, where it lives) with its tools (View in 3D,
  Remove base, Process on PC) on every tab. The Preview tab is just the image
  and the scan renders.
- The import options moved into a bottom drawer: one summary line always
  visible, Options to expand them, Folder to browse the save folder as a tree
  (projects and files with sizes; double-click opens a file). The drawer sizes
  itself so the project list and preview always keep their space.

## 0.9.2 (local)
- WiFi card redesigned: the code as four tiles, a pulsing status, the incoming
  project's name and thumbnail, a live speed graph that fills with progress
  (peak and current shown; the rate is now measured per second, not averaged),
  received / files / speed / time-left stats, and a New code button.
- A WiFi transfer that was received but never imported (app closed, picker lost)
  is offered again at the next start instead of sitting in a hidden folder.
- Preview panel: the render strip and tools row no longer get pushed off the
  bottom by a large preview image; project cards stop clipping the badge.

## 0.9.1 (local)
- Projects already on this PC (from USB or WiFi) show in the list with an
  "on this PC" badge even with no scanner attached, so preview, View in 3D,
  Remove base, Process on PC and Export ZIP work without the cable.
- WiFi dialog: firewall hint reads properly and disappears once the scanner is
  found; picker window sized to its rows; Files tab labels flat meshes correctly.

## 0.9.0 (local)
- **WiFi import, no cable.** A WiFi button next to Connect shows a 4-digit code;
  on the MIRACO choose Share to PC > Wi-Fi and enter it, and the project comes
  straight into PointYoink (about 20 MB/s, a 1 GB project in under a minute,
  faster than the cable). It then goes through the same import as USB: models
  only or full project, cleanup, STL/OBJ/GLB. Set your own code in Settings or
  get a fresh random one each time. Needs port 9706 (UDP and TCP) open.
- After a WiFi transfer a picker shows every scan with its mesh, point cloud
  and raw-frame sizes: tick what to keep, Models only or Full project.
- **Live tab** now has a source picker: MIRACO (pose and IMU over WiFi) or a
  tethered **Revopoint RANGE** over USB. The RANGE works natively on Linux with
  no vendor software: depth, both IR cameras and the color camera live, side by
  side or one at a time, a Combined depth-over-color view, a rotate control,
  and Capture, which saves the current frame as a point cloud (.ply) plus a
  color snapshot into `<save folder>/range/`. Needs `v4l-utils`. Plug the RANGE
  into a direct USB port, not a hub; it reboots itself whenever the stream
  stops, which is normal.
- The app honours `POINTYOINK_CONFIG` so test runs never touch real settings.

## 0.8.0 (local)
- **Process on PC**: rebuild a scan's mesh on your computer from the raw depth
  frames, on the GPU when available. Skips the scanner's slow on-device fusion
  and matches its output to about 0.2 mm. Uses frames already on disk from a
  full import, otherwise pulls just what it needs. Works on unfused scans too.
  Needs Open3D (optional, ~400 MB: `pip3 install --user --break-system-packages open3d`).
- Settings: Process on PC detail (voxel size; 0.4 mm matches the scanner).

## 0.7.0
- **Remove base**: an interactive cut-plane tool that slices the table/turntable
  off a scan and keeps the object (saves a cleaned `_clean.ply`, original kept).
- Optional **Clean up mesh** on import, in a memory-capped process so a huge
  mesh can never crash your machine.
- **Captures tab**: browse and pull the scanner's screenshots and screen recordings.
- A **Tools** row groups View in 3D and Remove base; a **status bar** shows activity.
- "Imported" now means a real model landed; partial export/ZIP failures are
  reported; safer device mount cleanup.

## 0.5.1
- 3D viewer now opens already-drawn instead of flashing a black window.
- Preview image scales to fit the window at any size.
- Project cards laid out on three lines so details are never cut off.
- Exported STL/OBJ/GLB files are named per project and scan, so they stay
  unique when collected in one folder.

## 0.5.0
- **View in 3D**: open any scan's mesh in an interactive window - drag to
  rotate, scroll to zoom, right-drag to pan. A loading indicator stays up until
  the model is actually on screen, and the mesh is auto-centered.

## 0.4.0
- Export ZIP: a button that bundles the selected project(s) into a single .zip
  in your save folder (raw frames skipped), for archiving or moving to another
  machine or slicer.

## 0.3.2
- Fix a crash that stopped the project list from rendering whenever the scanner
  had projects on it (an undefined variable in the list renderer). This made the
  app look like it connected but found nothing.
- HiDPI: read the GNOME desktop scale (and a POINTYOINK_SCALE override) so the
  window is sized correctly on scaled displays, not just from reported DPI.

## 0.3.1
- Fix a tiny window on HiDPI laptops by auto-detecting the display scale, plus a
  manual UI scale setting under Settings.
- Window no longer opens larger than the screen.
- Smarter re-import: shows an "updated" badge and skips the already-imported
  prompt when a project has changed on the device.
- Themed confirmation dialogs and animated progress during format conversion.
- Debian package (.deb) for one-command install.

## 0.3.0
- Rounded UI built on CustomTkinter, with an app icon, splash screen, and cleaner spacing.
- Auto-connects when the scanner is in File Transfer mode.
- Optional STL / OBJ export of the meshes on import.
- Rename a project to a readable name. The original scanner ID is kept as the
  folder name and reference so nothing is lost.
- Remembers which projects you have already imported, across sessions, and
  asks before importing one again.
- Bigger About panel: supported devices, requirements, and an inline changelog.
- Preview and Files tabs, per-project size, and an import summary.
- Settings that persist, plus Cancel and Retry during import.
- Licensed MIT.

## 0.2.0
- Project list with thumbnails and the scanner's own scan renders.
- Models-only vs full import, imported badges, and a destination picker.

## 0.1.0
- First working build: mount the MIRACO over MTP, pick projects, and pull them.
- Models-only mode skips the raw depth frames for a large speed-up.
