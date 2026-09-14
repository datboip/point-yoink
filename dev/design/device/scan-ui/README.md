# The scanner's own live-scanning screen (2026-09-13)

live-scan-screen.png: the screen while scanning. Three panels plus a distance strip:
- Top strip: a live histogram across five zones, "Too Near / Excellent / Good / Far / Too Far",
  showing where the current frame's depth pixels fall. This is the main thing to recreate: a
  glanceable readout of whether you're at the right distance, updated every frame.
- Top-left panel: a grey confidence/point view of the object with a blue outline over the part
  that reads "too near".
- Bottom-left panel: the colour camera with three controls, each with its own Auto toggle:
  a sun icon (exposure), WB (white balance), a lightning bolt (light/flash).
- Centre: the model building live as a point cloud while frames come in.
- Top-right icons: a cut-plane icon (matches our Remove base), a ruler (measurement, planned),
  a three-circle icon (overlap/merge, matches our Combine), an axis gizmo (matches ours).
- Right side: Continuous vs Single Shot capture, the shutter, a confirm tick, a "Model" toggle.

scan-settings-*.png: the Scan Settings panel.
| Setting | Options | Notes |
|---|---|---|
| Accuracy | High / Standard / High-speed (15 fps) | changes what Object Type offers |
| Alignment | Feature / Marker | Feature only in High-speed mode; matches our Auto-detect / Click spots in Combine |
| Object Type | General / Dark (High, Standard) or Body / Large (High-speed) | the set changes with Accuracy |
| Color | on/off | off by default in this session |

The scanner's raw depth frames carry no camera images (confirmed all session), so the confidence
view, exposure controls and distance histogram are all things the device computes for itself and
never sends over. In PC mode we get the depth and IR frames directly, so the distance histogram is
the one piece we can genuinely recreate: added to Live view as of this note (see CHANGELOG).
