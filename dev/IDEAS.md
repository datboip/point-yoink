# Post-1.0 ideas (parked, not committed to)

Loose ideas raised while using the app. Not on the 1.0 path — revisit after release.

- **Import history / provenance.** A small log or panel showing when each project was
  imported and from where (USB vs WiFi, scanner IP, models-only vs full). Would let you
  see the trail of what came in and how. (raised 2026-09-15 while testing the marker scan)

- **Lasso / brush select-and-delete in the 3D view.** Manual removal of strays the auto
  isolate can't catch: drag a lasso (or paint with a brush) in the GPU view, project the
  screen region onto the mesh, delete those faces, re-render, with undo. Already on the
  README roadmap. Biggest of the cleanup asks — a real interactive feature, best as its
  own focused build. (raised 2026-09-15)
- **Overlap removal in Prepare.** Multi-pass scans leave doubled/overlapping surface. The
  scanner's Fusion has an "overlap" control (dev/design/device/scanner-fusion-overlap.jpg).
  Investigate a self-overlap merge (voxel re-fuse, or o3d remove_duplicated + a shrinkwrap)
  beyond the current vertex/face dedupe. (raised 2026-09-15)

- **Remove marker marks (click-to-repair).** Registration stickers leave little
  craters/bumps that global smoothing can't fix without softening real edges.
  Flow (from the user, 2026-09-15): 1) click a marker pit/bump, adjust a small
  selection radius; 2) remove the damaged patch incl. its raised rim; 3) rebuild
  the patch from the surrounding surface — flat where flat, curved where curved
  (reuse the constrained-fill smoothing from process._smooth_fill_faces); 4)
  preview, accept or undo; everything outside the patch untouched. Start with
  click-to-repair, then optional auto-detect with HIGHLIGHTED candidates (never
  auto-remove every round feature — real screw holes look the same). Caveat: it
  invents surface the scanner never saw under the sticker; near a clip/sharp
  edge, rescanning without the sticker beats repair. Shares the picking + patch
  machinery with the lasso select-delete idea. Ref: Revopoint's marker-removal
  (remove affected area, fill by surrounding curvature).
