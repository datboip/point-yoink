# Road to 1.0.0 (first public push since 0.7.0)

Everything since 0.7.0 is local. 1.0.0 is a decision, not a version count. Do these, in order:

## Finish
- [x] Mock-based layout landed, GPU 3D view in the preview (glview.py), software fallback (meshview.py).
- [x] Two pages: Import (the scanner) and Projects (this PC) with the NEXT bar and the five steps
      Build > Cut base > Combine > Prepare > Export. First-run "How this works" panel, Help rewritten.
- [x] Cut-plane tool inside the window (GPU view, colours, remembered plane, applied when combining).
      The matplotlib tool (cutplane.py with a window) stays as the software-view fallback.
- [x] Prepare with live linked before/after and Keep/Discard; Export dialog with size and mesh check.
- [x] Combine: point pairs or Auto, ICP, overlay, editable, multi-set fusion (fuse.py --set).
- [ ] Shaded thumbnails for the list (scanner previews are used today; combined gets a render).
- [ ] The MIRACO's own screenshots in Help and the README (dev/design/device/*.png exist).
- [ ] WiFi transfer window: Run in background; review window with the raw-only warning (concepts).
- [ ] Menu: fold "Also export as" on Import into the Export dialog? (decide; STL on import is handy)

## Test on real hardware
- [ ] MIRACO USB import of a fresh project, models only and full (the user's save folder was emptied
      on 2026-09-11 for exactly this: import one project and walk the NEXT bar end to end).
- [ ] MIRACO WiFi share: single project, and check whether the project list allows a multi-select share.
- [x] Process on PC on a full scan (GPU), compared against the scanner's mesh: 0.2 mm median with the
      scanner's global poses (dev/design/device/SCANNER-EDIT-OPTIONS.md has the table).
- [ ] Cut base on each scan, Combine three sides, Prepare the combined model, Export STL, open in a slicer.
- [ ] A raw-only project (never One-tap edited): badges say raw only, NEXT says Build, Build works.
- [ ] RANGE: Live view, orientation confirmed, capture to .ply.
- [ ] Window at 1024x600: nothing clipped.

## Package
- [ ] Rename the GitHub repo to `point-yoink` (GitHub redirects the old name); update the remote,
      README links/badges and the install snippet's download URL.
- [ ] `packaging/build-deb.sh`, install from the LOCAL .deb (never `gh release download`, it
      inflates the counter), launch from the desktop entry, WiFi and USB both work from the install.
- [ ] CHANGELOG: collapse the 0.8.x / 0.9.x local entries into the 1.0.0 notes.
- [ ] Screenshot for the README from the release build (dev/render.py, no desktop capture).

## Release
- [ ] VERSION = "1.0.0", commit, tag v1.0.0, push main + tags, GitHub release with the .deb attached.
- [ ] Plain commit message and release notes. No AI attribution lines.
