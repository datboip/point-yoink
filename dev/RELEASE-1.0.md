# Road to 1.0.0 (first public push since 0.7.0)

Everything since 0.7.0 is local. 1.0.0 is a decision, not a version count. Do these, in order:

## Finish
- [ ] Mock-based layout landed (build round winner + grafts), with the embedded 3D view
      (meshview.py) in the preview and shaded thumbnails (shade.py) in the list and scan strip.
- [ ] Walkthrough / first-run guide: the one-click path for most people (plug in or WiFi, tick, Import),
      with the MIRACO's own screenshots (share icon, Share to PC Wi-Fi code screen, Transfer finished,
      USB File Transfer popup). Same images in the README.
- [ ] Editor mode (Process): cut-plane tool inside the window, clean-up with preview.

## Test on real hardware
- [ ] MIRACO USB import of a fresh project, models only and full.
- [ ] MIRACO WiFi share: single project, and check whether the project list allows a multi-select share.
- [ ] Process on PC on a full scan (GPU), compare against the scanner's mesh.
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
