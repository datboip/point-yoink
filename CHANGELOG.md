# Changelog

All notable changes to PointYoink. Versions before 1.0.0 are pre-release
builds; 1.0.0 is the first public release.

## 1.0.0 (unreleased, rc2 on the dev branch)

- Cancelling a WiFi Replace part-way now puts the previous project back instead of deleting it.
- The 3D viewer is no longer closed after 15 quiet minutes; the stall watch ends once it has drawn.
- Files still being written by Prepare, a rebuild or an export never show up as scans or land in a ZIP.
- A failed mesh write in the processing child now reports failure instead of exiting clean; its ordinary export path writes to a temp file and replaces only when complete.
- Two exports of the same name started back to back get separate temp files.
- Saving two mesh edits in the same second keeps both archived versions.
- The wrong-code lockout timer only closes the share it belonged to.
- A failure while starting Combine (cache folder unwritable, disk full) frees the controls and says why.
- Tilting the cut plane no longer flips its side or moves it off the pivot near the sign boundary.
- Package: PyOpenGL's Windows DLL folder is no longer installed; its license text ships under /usr/share/doc/point-yoink/licenses. README states the actual memory limits per job.
- The last glyph labels (search, Pull all, Browse, updated, Restore, info) use icons or plain text.

## 1.0.0 (unreleased, rc1 on the dev branch)

- Every button, menu item and step marker now uses the icon set; the last unicode glyph labels are gone, so the UI no longer depends on which symbols the system font happens to have.
The first public release, being finished on the dev branch.
- Pull Revopoint MIRACO scans onto Linux over USB (MTP) or WiFi. No Revo Scan, no Windows, no VM.
- Build the mesh on the GPU, remove the base, combine sides, prepare the surface, and export STL / OBJ / GLB / PLY, with a mesh check before you save.
- Captures: pull the scanner's screenshots and recordings; per-project records, renaming and thumbnails.
- Remove base rebuilt: the 3D view on the left and a tool palette on the right. Table direction by floor grid, auto-detect, or three or more clicked spots (click a dot again to remove it, Backspace undoes the last, Clear points wipes them; spots survive switching modes). Cut height by slider or an exact millimetre field; fine tilt (pitch and roll) the same way; Flip side; Reset really deselects everything.
- A base cut can be undone from the same dialog. Applying a cut now keeps a copy of any prepared model it replaces.
- Scans are named by their own id unless you name them, the same as projects. Naming a project uses the same dialog as naming a scan.
- Every popup opens centred inside the main window and never larger than it on first open.
- Buttons show a working state while a job runs instead of greying out; hover contrast on the blue buttons fixed.
- The splash spinner is drawn smoothly and no longer looks stuck while the window is still being built.
- The experimental live view and the RANGE camera source are not part of the shipped app.

## 0.9.56 (dev, 2026-09-15)
- **The freeze, actually fixed.** Every "app frozen / keyboard dead for a minute" report traced to one
  thing: Tk creates a real X window per widget and, with GNOME's default `XMODIFIERS=@im=ibus`, made a
  synchronous X Input Method round-trip to `ibus-daemon --xim` for each one at ~100 ms a reply (native
  stack: `XCreateIC -> _XimProtoCreateIC -> _XimRead`). A plain-tkinter control went from 20+ s to
  0.05 s for 80 widgets with the IM disabled; app startup went from 26-59 s to 0.09 s. Because ibus is
  also every other app's keyboard path, the flood stalled typing system-wide. PointYoink now sets
  `XMODIFIERS=@im=none` before Tk opens the display (`POINTYOINK_XIM=1` restores it) and calls
  `tk useinputmethods 0`; child Tk processes inherit it.
- Guard against CustomTkinter's scrollbar redraw re-entrancy: `CTkScrollbar._draw()` ends in a
  synchronous `update_idletasks()` that can start another scrollbar's redraw mid-draw and cascade across
  every scrollable frame. Only the outermost draw now flushes idle tasks. Lists, the scan strip, the
  detail panel and Captures also unmap while they rebuild.
- The splash now waits for the real first paint (looped `update_idletasks()` until a pass finds no
  work) instead of a fixed 700 ms, guarded against running twice; never uses `update()`, which drains
  raw input events and can hang under a high-poll-rate mouse.
- Closing no longer calls `update_idletasks()` before `os._exit(0)` (the close-freeze).
- Single-instance lock (`~/.config/pointyoink/pointyoink.lock`): a second copy shows "already running"
  instead of touching the scanner alongside the first.
- **Live 3D preview is back on by default** (`"auto_live_preview": false` in config to opt out). It was
  switched off in 0.9.49 because scan clicks "froze" the app - that was the ibus stall above, not the
  3D view. Three things were wrong with the view itself and are fixed:
  - it could never appear: the GL widget only uploads the mesh once it is on screen, but the app only
    put it on screen after the upload reported ready. The widget now maps underneath the still image
    while it loads, and the swap is instant (0.9 s load+upload for a 550k-triangle scan; the extra
    1.8 s is the deliberate "load when idle" pause);
  - inside the app the GPU context failed every time with `GLXBadDrawable`, and that X error killed
    the whole process. Cause: pyopengltk talks GLX on its own second X connection, and Tk delivers
    `<Map>` for a child window before its `CreateWindow` has left Tk's output buffer, so the server had
    never seen the drawable. The view's X window is now created at construction and a real round-trip
    precedes the GLX call;
  - an X error during context creation is now caught (temporary Xlib error handler) and turns into
    the existing software-view fallback instead of exiting the app. Same protection for the
    Remove-base and Combine windows, which use the same widget.

## 0.9.55 (dev, 2026-09-14)
- Moved automatic Solid/Wireframe shaded preview rendering out of the Tk process and into an app-owned `shade.py` child process with BLAS/OpenMP thread caps. The GUI now keeps the nicer grid preview behavior without importing/running the heavy mesh renderer inside the UI process.

## 0.9.54 (dev, 2026-09-14)
- Removed the repeating idle USB refresh probe. PointYoink now does one safe startup device-state check, then waits for explicit USB/Rescan/Refresh actions instead of waking every few seconds while the user is working.
- Fixed misleading `slow ui: refresh_probe took ~5000 ms` logging; slow UI logs now measure the actual queue handler duration instead of time elapsed until the next queue event.

## 0.9.53 (dev, 2026-09-14)
- Restored automatic Solid/Wireframe mesh previews by default, while keeping the setting hook to disable them if needed.
- Removed the hidden full Prepare-page rebuild from ordinary project selection; selecting a project now refreshes only the visible right-side panel unless the full cards view is explicitly opened.
- Replaced click-time raw-frame glob counting with a cached fast existence check, so selecting scans no longer walks thousands of raw frame files just to update labels/buttons.
- Prevented combined-model gallery tiles from rendering meshes unless mesh previews are enabled, avoiding surprise mesh work in the gallery loader.

## 0.9.52 (dev, 2026-09-14)
- Disabled automatic mesh rendering in the main preview and Prepare cards by default. Selecting a project, selecting a scan, startup, and Refresh now keep using cheap scanner/static preview images instead of loading/decimating/rendering mesh files inside the GUI process.
- Kept explicit 3D actions available: View in 3D still loads the model when the user asks, but background preview rendering no longer starts just because selection changed.

## 0.9.51 (dev, 2026-09-14)
- Stopped passive refresh from touching the MTP/FUSE mountpoint. The 5-second background poll now reads only sysfs and /proc/self/mountinfo, so a stale jmtpfs mount cannot be hammered every 1.5 seconds while the app is open.
- Disabled automatic USB mounting from the poll loop; scanner filesystem access now starts only from explicit USB/Rescan/listing actions.
- Removed the automatic screenshots/recordings load after project refresh; Captures now touches MTP only when the user explicitly refreshes/pulls captures.
- Made Refresh refresh the current safe source and throttled scanner rescans so repeated clicks cannot machine-gun the MTP mount.

## 0.9.50 (dev, 2026-09-14)
- Added a small lifecycle safety layer for app-owned child processes: View in 3D, Remove Base, align/register/fuse, mesh info, quick draft builds, cleaning, and full imports are now tracked so app close can terminate them instead of leaving heavy work orphaned.
- Bounded the UI queue pump so event bursts yield back to Tk instead of monopolizing the UI thread.
- Snapshotted cleanup/build settings on the Tk thread before workers start, so background workers no longer read live Tk variables.
- Made loader/base/fuse completion events carry ownership context, preventing stale background work from closing a newer loader or updating the wrong project after selection changes.
- Moved screenshot USB mount probing off the UI thread and made screenshot refresh clear its busy state on every completion path.
- Moved the optional Open3D dependency probe into an app-owned background check so the first Build/Combine/Auto click cannot freeze the UI while Python imports Open3D.

## 0.9.49 (dev, 2026-09-14)
- The interactive live 3D view no longer auto-loads the instant a scan is
  selected. That was firing on every single click regardless of intent, and
  is the most likely real explanation for "click a scan and it takes
  forever" - the fast still-image preview shows immediately as before, and
  the full interactive view now only loads when you explicitly click
  "View in 3D" (a separate code path, unaffected by this change and
  confirmed still working). Scan-preview work is also now debounced so
  rapid clicking through scans doesn't queue up a render per click.
- Every background worker that could previously die silently (refresh
  probe, project listing, mount) now catches its own exceptions and
  reports failure through the UI instead of leaving things stuck with
  nothing in the log.
- Patch from a code review, verified directly before shipping: confirmed
  selecting a scan no longer calls _mv_start() at all by default, and the
  full smoke suite passes clean.

## 0.9.48 (dev, 2026-09-14)
- Found and fixed the real, systemic cause of tonight's recurring freeze
  reports: refresh_loop() called quick_mounted() - which spawns a real
  subprocess (ls against the MTP mountpoint) - directly on the UI thread,
  every 1.5 seconds, continuously, for the entire time the app is open, not
  just on a user action. A stale mount (jmtpfs left over from an earlier
  session with no scanner attached, confirmed live: it was returning a real
  I/O error) can make that single call take seconds, repeating every cycle.
  This is very likely why the freeze seemed to correlate with almost
  anything - clicking Refresh, clicking a scan, even just opening the app -
  the user was just as likely to interact right as one of these
  already-running periodic checks happened to be blocking.
  start_listing() (called by the Refresh button and by every project list
  load) had the same issue and is fixed the same way. Both now run on a
  background thread and report back through the queue. Verified directly
  with a simulated 2.5s-slow quick_mounted(): refresh_loop() used to block
  its caller for the full 2.5s, now returns instantly and the UI thread's
  update() calls never gap by more than ~0.1s while the slow probe runs in
  the background.

## 0.9.47 (dev, 2026-09-14)
- Capped the main interactive 3D preview's mesh detail at 300,000 faces
  (matching Remove Base's existing cap, which the main preview never had -
  it fell back to GLView's 3,000,000-face default, meaning a 500-650k
  triangle scan never got decimated at all). Measured the actual mesh
  load and normal computation at well under a second either way, so this
  is a safety margin for larger scans, not a proven fix for tonight's
  reported stall.
- Added RAYON_NUM_THREADS to the thread cap set at startup: the BLAS-only
  variables added earlier never touched fast_simplification (Rust/rayon),
  a real gap. Measured directly that this library runs single-threaded
  for a realistic decimation regardless, so it wasn't actually bursting
  cores either - added anyway since it costs nothing and closes a real
  hole in the original fix's coverage.

## 0.9.46 (dev, 2026-09-14)
- Fixed "Still image - the live 3D view is loading" staying up forever with
  no way to tell whether it's genuinely still loading or already failed:
  the failure branch reused the exact same text as the in-progress state,
  and never logged anything either. Now shows a distinct "couldn't load"
  message and logs the actual reason.

## 0.9.45 (dev, 2026-09-14)
- Fixed the exact bug behind the "Drawing the 3D model…" blank right panel,
  found with a code audit: `_panel_refresh`, `_proc_render`, `_next_refresh`,
  and `render_list`/`render_shots` all destroyed their widgets before
  rebuilding, with nothing catching an exception partway through - the panel
  was left permanently blank with no error anywhere (a Tk-callback
  exception, which our own drain_loop try/except doesn't see). All five now
  catch that and show a clear "couldn't refresh, see the log" message
  instead of going silently empty. One real bug already turned up during
  this: `render_list`'s split introduced a broken reference the smoke suite
  caught immediately, fixed before shipping.
- Cached the Open3D availability check: every Build/Combine button ran it
  directly on the UI thread, uncached, and it can take up to 60 seconds
  worst-case (it spawns a subprocess to probe without loading the ~400MB
  library into the app itself). Now checked once per run and reused.

## 0.9.44 (dev, 2026-09-14)
- Fixed "Could not load this model" in Remove Base and other GPU-view
  dialogs: GLView.load() could report success before the mesh was actually
  uploaded to the GPU, if the view widget wasn't mapped/ready yet at the
  moment loading finished (a real timing race, not a bad file - confirmed
  the same mesh loads fine outside the app). The dialog's own check for a
  missing view._src would then wrongly show "Could not load this model"
  for a load that was actually still in progress. on_ready now only fires
  once the upload genuinely completes, success or failure, never before.
  Verified directly: on_ready no longer fires while the upload is still
  deferred, only once it actually runs.

## 0.9.43 (dev, 2026-09-14)
- Found and fixed the real cause of tonight's recurring "app is frozen, I can
  move the window but can't click anything" reports. `_modal()` (used by
  every confirm/alert dialog) and `_ask_zip_format()` are the only two
  dialogs in the app that take an input grab, and neither had a
  WM_DELETE_WINDOW handler - closing one via the dialog's own window-manager
  close button skipped the code path that destroys it, so the grab was
  never released. Everything else in the app kept running fine (that's why
  the process never looked hung), but all clicks and keystrokes were
  silently swallowed by the dead dialog's grab. Both now handle the window
  manager's close button the same way as clicking a button, and release
  the grab explicitly in a finally block as a second safety net. Verified
  directly: simulating a window-manager close request now destroys the
  dialog and releases the grab every time.

## 0.9.42 (dev, 2026-09-14)
- Reverted the WiFi speed graph's x-axis back to overall progress (grows
  left to right as the transfer completes, like the old Windows copy
  dialog) instead of 0.9.39's always-full-width sample scaling and 0.9.41's
  added progress marker - both were solving a problem the user didn't
  actually have and introduced a more confusing one. Kept the one real fix
  from 0.9.39: the reference gridlines are never painted over on the
  not-yet-reached side anymore.

## 0.9.41 (dev, 2026-09-14)
- Fixed a corrupted saved window size (a "1x1" geometry got persisted,
  probably from a restart at the wrong moment) that made the window open
  tiny and threw the splash's centering off-screen. _persist() now refuses
  to save a degenerate size and keeps the last good one instead, and the
  splash ignores a bad saved size rather than centering on it.
- The WiFi speed graph's 0.9.39 full-width redraw fixed the ugly blank
  space, but lost any sense of overall progress - it always looked "100%
  done" regardless of actual transfer percentage. Added a thin marker line
  showing real progress on top of the speed history.

## 0.9.40 (dev, 2026-09-14)
- Fixes from an external audit, each verified against the real code before
  changing anything:
  - USB mount cleanup now only unmounts our own scanner's gvfs MTP claim
    (matched by USB bus/device number), never a phone or other camera also
    plugged in - the old code matched any mtp:// mount it saw despite its
    own comment saying otherwise.
  - Closing the app while Live view is connected now actually stops the
    camera stream first. os._exit(0) does not kill child processes, so the
    v4l2-ctl stream would previously outlive the GUI and keep holding the
    camera.
  - A "models only" import of a project with no built model yet used to
    silently report "Import complete." with nothing actually copied. It now
    says so plainly and tells you to build it on the scanner first.
  - Config saves are now atomic (write to a temp file, then rename) instead
    of writing the real file in place, so a crash or power loss mid-save
    can't leave a corrupt config.

## 0.9.39 (dev, 2026-09-14)
- Fixed the WiFi receive dialog's "speed"/"time left" row captions getting
  clipped: the connected-state window was a fixed 520x560, but the actual
  content needs ~590px with its padding. Measured the real requirement and
  set it to 600.
- Fixed "peak" in the speed stat missing its unit ("peak 44" -> "peak 44
  MB/s").
- Redrew the speed graph: it used to scale its x-axis by overall transfer
  progress, leaving a big painted-over blank rectangle (and hidden
  reference gridlines) for whatever hadn't arrived yet. It now spreads
  whatever samples exist across the full width, so the gridlines stay
  visible throughout and the chart never shows a large empty box.

## 0.9.38 (dev, 2026-09-14)
- Fixed the app pegging every CPU core for 10-20 seconds during things like
  a post-delete refresh, freezing keyboard input system-wide (video kept
  playing fine since that's GPU-driven, but typing anywhere froze). Every
  subprocess we spawn for heavy work already capped BLAS threading
  (OPENBLAS_NUM_THREADS=1), but in-process numpy/scipy/trimesh calls never
  did, so a single mesh-stats or thumbnail pass could spawn one thread per
  core. Capped OMP/OPENBLAS/MKL/NUMEXPR threads to 2 for the whole app at
  startup; verified OpenBLAS actually honors it before shipping.

## 0.9.37 (dev, 2026-09-14)
- Found and fixed the same freeze pattern as 0.9.36 in four WiFi receive
  spots (cancel, an empty/no-project finish, the orphaned-transfer
  recovery check, and discarding a received project): each deleted a
  staging folder directly on the UI thread, which can hold a partial
  multi-GB scan. All four now clean up on a background thread instead.

## 0.9.36 (dev, 2026-09-14)
- Fixed a real freeze on "Delete project"/"Delete this version": both called
  the trash move directly on the UI thread with no time bound on its
  fallback (a real copy+delete if the trash folder lands on a different
  filesystem than the project), so deleting anything but a small project
  could freeze the whole window for as long as the move took. Both now run
  the move on a background thread and report back through the queue like
  every other slow operation in the app; verified with a simulated slow
  trash op that the UI keeps responding throughout.

## 0.9.35 (dev, 2026-09-14)
- Live view now notices when the scanner disappears mid-session (unplugged,
  powered off, or a stream that just stopped producing frames) instead of
  leaving the panel stuck showing the last frame forever with a dead
  Connect/Disconnect button. It resets its own connection state, cleans up
  the streams, and tells you to plug the scanner back in and reconnect -
  the physical USB/device itself is never touched, only our side's state.

## 0.9.34 (dev, 2026-09-13)
- Fixed a real ~10 minute stall on MIRACO connect introduced in 0.9.33: the
  three new color-alignment reads (LC_RT.bin/Prgb.bin/Distort.bin) had no
  timeout, so a device/firmware without one of those files could hang the
  whole connect sequence for minutes before the projector even turned on or
  streaming started. They now run after the stream is already live, each
  capped at 4 seconds, and update the status to "aligned" if they succeed
  instead of blocking anything if they don't.

## 0.9.33 (dev, 2026-09-13)
- MIRACO PC-mode Live view: the Combined panel now does a real depth-to-color
  reprojection using the scanner's own factory calibration (LC_RT.bin
  extrinsics, Prgb.bin intrinsics, Distort.bin lens distortion - read the
  same safe way we already read the depth calibration file) instead of a
  plain resize-and-blend guess. Falls back to the old overlay if any of that
  calibration can't be read.

## 0.9.32 (dev, 2026-09-13)
- Fixed Live view opening with "MIRACO (PC mode, USB)" shown as selected but
  the WiFi position panel showing underneath (the source picker's default
  selection never told the panel to match it).

## 0.9.31 (dev, 2026-09-13)
- Fixed a real freeze at the root: an exception in ANY background-result
  handler (project listing, thumbnails, WiFi, builds, ...) used to silently
  kill the queue pump forever - the window would just stop updating, most
  visibly as the splash never closing, with no error shown anywhere but the
  log. One bad event now gets logged and skipped; everything else keeps
  working.

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
