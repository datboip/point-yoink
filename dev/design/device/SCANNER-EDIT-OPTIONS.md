# MIRACO Pro on-device editing: every option, as text

Firmware V1.0.0.129. Written down from screenshots (scanner-fusion*.jpg,
scanner-mesh*.jpg in this folder) so nobody needs to re-take or re-send them.
Values shown are the device defaults for a "High / Feature / General, Camera
Module: Near" scan (project 09112026040253, 438 frames, 110,951 fused points).

## Project screen
Left column = the pipeline, one stage lit at a time:
  Raw Data -> Fused -> Meshed -> Textured
Right column = the actions: One-tap Edit (magic wand, runs the whole chain with
defaults), Fusion, Mesh, Texture. Top right: Resume Scan, share icon.
Undo / redo arrows bottom right. Rectangle / Lasso selection tools appear under
the panel once a point cloud or mesh exists. "< 4 / 4 >" pages through scans.

## Fusion panel (Raw Data -> Fused point cloud)
Four tabs. The header shows Frames: N while on raw data, Points: N once fused.

| Tab | Control | Default | Button |
|---|---|---|---|
| Fusion | Fusion Method: Standard / Advanced | Standard | Apply |
| Fusion | Point Distance (mm), slider | 1.09 | Apply |
| Isolation | Isolation rate, slider, percent | 15% | Detect |
| Overlap Detection | Overlap Distance (mm), slider | 1.16 | Detect |
| Smooth | Strength, slider | 10 | Apply |
| Smooth | Times, slider | 10 | Apply |

Each slider has a reset-to-default circular arrow next to its label.
"Detect" tabs highlight what would be removed, then you confirm; "Apply" tabs
change the data straight away.

## Mesh panel (Fused -> Meshed)
Four tabs.

| Tab | Control | Default | Button |
|---|---|---|---|
| Mesh | Quality, slider 1..7, shows the resulting Grid Size | 7.0 (Grid Size 0.54 mm) | Apply |
| Mesh | Hole Filling (Auto), toggle | off | Apply |
| Mesh | Ratio, slider, percent (simplify at mesh time) | 30% | Apply |
| Isolation | Isolation rate, slider, percent | 15% | Detect |
| Simplify | Ratio, slider, percent | 40% | Apply |
| Smooth | Strength, slider, percent | 30% | Apply |
| Smooth | Times, slider | 3 | Apply |

## What that means for PointYoink's Process page
Our chain is: raw frames -> fuse.py (Open3D TSDF, voxel = "Point Distance",
0.4 mm default) -> process.py --clean (dedupe, keep largest piece, fill holes,
Humphrey smooth 5 iterations).

Mapping, in the scanner's words:
- Point Distance (mm) = our fuse voxel size. Scanner default 1.09 mm; we use
  0.4 mm, which is why our raw build has more detail and more noise.
- Isolation rate 15% = drop every connected piece smaller than 15% of the
  largest one. We keep only the largest piece (100%); 15% is safer for
  objects scanned in parts.
- Overlap Distance 1.16 mm = merge points closer than this from overlapping
  passes. TSDF fusion does this implicitly; nothing to add.
- Fusion Smooth 10 / 10 = point-cloud smoothing before meshing. We do not do
  this; our smoothing happens after meshing.
- Mesh Quality 7 = Grid Size 0.54 mm (the meshing cell). Ours is the marching
  cubes cell from the voxel size.
- Hole Filling (Auto) off by default. We fill small holes always; make it a
  toggle, default off to match.
- Ratio 30% at mesh time and Simplify 40% = keep that share of faces. We have
  --decimate N (a face count); expose it as a percent instead.
- Mesh Smooth Strength 30% / Times 3 = Humphrey/Laplacian with 3 iterations;
  our 5 iterations at default strength is a little heavier.

So One-tap Edit is roughly: fuse at 1.09 mm, isolate 15%, smooth 10x10, mesh
at 0.54 mm, no hole fill, simplify to 30%, smooth 30% x3. Measured 2026-09-11:
our build agrees with One-tap Edit to about 0.5 mm median but leaves 5548
pieces vs 23, i.e. isolation is the missing step that matters most.

## Measured 2026-09-11, after the global-pose fix in fuse.py
The scanner's fuse_mesh.ply is its fused point cloud (fuse.ply) with a skin
(0.13 mm apart), so every difference is in fusion, not meshing. Our builds
used the per-frame .inf poses; the device fuses with cache/
global_register_pose.pose (int32 count; per entry int32 pass, int32 frame,
4x4 float64 camera->world in mm). After a Resume Scan the second pass was
47 mm off in .inf. With the global poses, cleaned build vs One-tap Edit:

| scan | passes | median mm | 95% mm | scanner surface >1 mm from ours |
|---|---|---|---|---|
| 09112026025559 | 3 | 0.23 | 1.37 | 15% |
| 09112026030031 | 2 | 0.17 | 1.16 | 6% |
| 09112026030352 | 2 | 0.19 | 1.52 | 7% |

Before the fix the same scans gave 0.53 to 0.83 mm median and 26 to 45%.
Voxel size (0.4 / 0.6 / 1.0 mm), min-weight and smoothing changed almost
nothing. Automatic base removal (RANSAC plane) cut into this part itself
(flat face wins), so it must stay a manual tool. Our result still carries 9
to 20% extra surface the scanner trims (floor patches), so that is the next
gap: their trim step, not our fusion. This scan's property.rvproj says
point_pitch 0.32 mm, so the 1.09 mm in the screenshots was that other
project's default, not a constant.

## Measured 2026-09-13: what made One-tap Edit look better, and the fix
Same-orientation renders showed the real difference: our build had a second skin under the
flat panel and the panel itself came out ragged / inside out. Not smoothing: Taubin and
Laplacian changed nothing that mattered. Not the two passes: each pass alone had the extra.
Cause: the voxel fusion took every depth pixel (edges, flying pixels, grazing angles) and the
8-voxel truncation band let a thin sheet seen from both sides cancel. fuse.py now defaults to
--filter 3 (drop pixels at depth jumps > 3 mm with a 1-px skirt, and where the surface tilts
more than --grazing 80 deg from the view ray), --trunc 3 voxels, --min-weight 3.
Cleaned build vs One-tap Edit, voxel 0.4 mm (extra = our surface > 1 mm from theirs):

| scan | ours extra | 95% | scanner missing from ours |
|---|---|---|---|
| 09112026025559 | 1.1% | 0.31 mm | 5.6% |
| 09112026030031 | 0.7% | 0.29 mm | 7.5% |
| 09112026030352 | 0.7% | 0.31 mm | 13.8% (half of it is their floor patches) |

Before: 20% extra with 95% at 8.7 mm. Poisson on the scanner's own fuse.ply reproduces their
fuse_mesh.ply to 0.1 mm, so their meshing is Poisson-like; Poisson on our points is an option
for later (closes holes, single skin) but the input filtering was the actual gap.

## Device screens added 2026-09-13
- scanner-project-scans-status.png: the project view lists each scan by its number with a status
  label, "Meshed" or "Raw Data", the same states our badges show (scanner-edited / raw only).
- scanner-wifi-sharing.png: Share to PC > Wi-Fi during a transfer: the code, "Sharing, keep the
  screen on", and the note that shared projects may contain unedited models.
