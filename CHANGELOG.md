# Changelog

All notable changes to PointYoink. Versions before 1.0.0 are pre-release
builds; 1.0.0 will be the first public GitHub release.

## 0.9.4 (local, unreleased)
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
