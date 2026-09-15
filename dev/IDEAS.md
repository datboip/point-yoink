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
