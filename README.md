# PointYoink

[![Release](https://img.shields.io/github/v/release/datboip/pointyoink?color=4aa3ff)](https://github.com/datboip/pointyoink/releases)
[![Downloads](https://img.shields.io/github/downloads/datboip/pointyoink/total?color=3ecf8e)](https://github.com/datboip/pointyoink/releases)
[![Stars](https://img.shields.io/github/stars/datboip/pointyoink?color=ffb454)](https://github.com/datboip/pointyoink/stargazers)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Linux-informational)

**Get your Revopoint MIRACO 3D scans onto Linux, without Revo Scan, Wine, or a Windows VM.**

Revopoint's Revo Scan software is Windows, macOS, iOS and Android only. There is no Linux version, so getting your finished scans off a MIRACO on a Linux machine usually means dual-booting, running a Windows VM, or fighting a raw MTP copy that drags thousands of tiny files across a slow connection.

PointYoink is a small desktop app that does it directly. Put the scanner in File Transfer mode, and PointYoink mounts it, shows your projects with the scanner's own preview renders, and copies the ones you pick straight to a folder. The output is standard `.ply` meshes and point clouds you can open in Blender, MeshLab, or CloudCompare.

> Unofficial. Not affiliated with or endorsed by Revopoint. "Revopoint" and "MIRACO" are trademarks of their respective owners. PointYoink only reads files off your own device and contains none of Revopoint's software.

## Screenshots

<p align="center">
  <img src="images/splash.png" alt="PointYoink" width="440">
</p>

<p align="center">
  <img src="images/app_connected.png" alt="PointYoink main window - connected, with projects and a scan preview" width="920">
</p>

## What it does

- Detects the scanner and its File Transfer (MTP) mode, and connects automatically.
- Lists your projects with thumbnails, dates, scan counts, and sizes.
- Shows the scanner's own preview render for each scan, and opens any mesh in an interactive 3D viewer (rotate/zoom).
- Copies just the finished models by default, so you move megabytes instead of gigabytes. Or turn that off to pull the full project including raw frames.
- Optionally exports the meshes to STL, OBJ, or GLB on import.
- **Process on PC**: rebuild a scan's mesh on your computer from the raw depth frames (GPU when available), skipping the scanner's slow on-device fusion. Works on scans you never fused on the device.
- Rename a project to something readable. The original scanner ID stays as the folder name and reference, so nothing is lost.
- Remembers what you have already imported, across sessions, and asks before importing it again.
- Export any project as a single .zip for archiving or sharing.
- Cancel and retry, per-project progress, an import summary, and a dark UI.
- Built-in error log to make bug reports easy.

## Why not just...

| Option | What it is | Why it falls short for a MIRACO on Linux |
|---|---|---|
| Revo Scan under Wine | The Windows app via Wine | Launches but usually cannot see the scanner over USB; version 5/6 crash. |
| Windows VM | A full Windows guest | Works but heavy, and USB passthrough is fiddly. Overkill for copying files. |
| jmtpfs / gvfs by hand | Mount MTP, copy manually | Works, but you drag every raw depth frame through slow MTP and have to know which folders to skip. |
| android-file-transfer | Generic Android MTP tool | No idea what a scan project is, so it copies everything, slowly. |
| revopoint-python | Controls an older Pop/MINI over WiFi | A live-capture tool for tethered scanners, not a MIRACO extractor. |

PointYoink is the one that understands the MIRACO's on-device project layout and pulls only the finished meshes and point clouds.

## Supported scanners

- **MIRACO Pro** - tested and working (this is what PointYoink was built and verified against).
- **MIRACO and MIRACO Plus** - same standalone design, should work, but not yet confirmed. Reports welcome.

These are the standalone MIRACO models that store finished projects on the device and expose them over USB. Tethered scanners (POP, INSPIRE, RANGE, MINI, MetroX) are not supported, because those keep their data on whatever computer or phone ran Revo Scan, not on the scanner. If you have one of those, your files are already on that machine.

## Requirements

- Linux with Python 3.10 or newer.
- System packages: `sudo apt install python3-tk python3-pil.imagetk python3-matplotlib python3-networkx jmtpfs rsync`
- Python packages: `pip install customtkinter pillow trimesh "pyglet<2" fast-simplification networkx matplotlib`
- Optional, for Process on PC: `pip3 install --user --break-system-packages open3d` (~400 MB; uses your GPU when available).
- A USB-C **data** cable. Some bundled cables only charge. If nothing shows up, try a different cable.

## Install

### Debian / Ubuntu (recommended)

Download the latest `.deb` from [Releases](https://github.com/datboip/pointyoink/releases), then install it (this pulls in the dependencies automatically):

```bash
sudo apt install ./pointyoink_0.8.0_amd64.deb
```

PointYoink then shows up in your application menu - launch it from there, or run `pointyoink`. No pip, no virtualenv.

### From source (any distro)

```bash
sudo apt install python3-tk python3-pil.imagetk python3-matplotlib python3-networkx jmtpfs rsync
git clone https://github.com/datboip/pointyoink
cd pointyoink
python3 -m venv --system-site-packages venv
./venv/bin/pip install customtkinter pillow trimesh "pyglet<2" fast-simplification networkx matplotlib
./venv/bin/python pointyoink.py
```

## How to use

1. Plug the scanner into the PC with a USB-C data cable.
2. On the scanner screen, tap **File Transfer** (Share to PC, USB Cable).
3. PointYoink connects on its own. Your projects appear on the left.
4. Tick the projects you want. Click one to preview its scans.
5. Choose a **Save to** folder and click **Import selected**.

**Models only** (on by default) copies the finished `.ply` meshes and point clouds and skips the raw depth frames. Turn it off only if you want the raw frames to reprocess a scan later in Revo Scan on another machine.

## How it works

A MIRACO project holds thousands of raw depth frames plus the finished, fused output. The raw frames are the bulk of the size and are only needed to re-fuse a scan. PointYoink copies just the finished output, so an import moves a few megabytes instead of gigabytes. It still uses `jmtpfs` underneath; the speed comes from not moving the data you do not need.

Imported files land in a clean, flat layout with unique names, so nothing is buried in cryptic folders:

```
revopoint-scans-models/
  Project09102026033917/
    Project09102026033917_<scan>.ply    # the mesh, one per scan
    Project09102026033917_<scan>.stl    # if you asked for STL/OBJ/GLB
    Project09102026033917_<scan>.png    # the scan's preview render
    Project09102026033917_<scan>_cloud.ply  # the point cloud
    Project09102026033917.revo          # project metadata
```

The `.ply` mesh is what you print or render; the `_cloud.ply` is the raw point cloud. Both are standard binary PLY and open in Blender, MeshLab, or CloudCompare. Turn off **Models only** to pull the full project including raw frames instead (kept in the scanner's original nested layout, for re-processing in Revo Scan).

**Export ZIP** bundles the selected projects, and asks what to include - STL only, OBJ only, GLB only, all models, or everything. Files go in flat with clean names, so unzipping gives you a ready-to-use folder (e.g. drop the STLs straight into a slicer).

## Process on PC

The MIRACO builds its 3D models on a phone-class chip, so it is slow and gives you few knobs. PointYoink can do that step on your computer instead: **Process on PC** builds a scan's 3D model from the raw scan data, on your graphics card when there is one (an NVIDIA card does a scan in seconds; the CPU takes minutes).

- Raw scan data comes over WiFi with **Full project** (over USB it is impractically slow, about 4 seconds per file).
- The result lands next to the project as `<name>_<scan>_pcfused.ply`, ready for the 3D view, Remove base and export.
- Measured against the scanner's own model of the same scan: the surfaces agree to about 0.5 mm where both exist. What the scanner's One-tap Edit adds on top is clean-up (floaters removed, base cut, holes filled, smoothed); in PointYoink those are separate steps for now, so a freshly built model is noisier than the scanner's until you run them. See `dev/compare.py`.
- Detail is set in Settings (voxel size; 0.4 mm matches the scanner, 0.3 is finer and uses more memory).
- Needs [Open3D](https://www.open3d.org/), which is large and optional: `pip3 install --user --break-system-packages open3d`. PointYoink tells you if it is missing.

## Troubleshooting

- **Nothing detected:** make sure you tapped File Transfer on the scanner, not just plugged it in. Try a different USB-C cable, since some only charge.
- **Connect fails or hangs:** unplug and replug, tap File Transfer again. PointYoink clears stale connections on its own.
- **Previews are blank:** the scanner is still waking up. Click the project again.
- Anything else: open **Log** in the app, copy it, and file an issue.

## Roadmap

- A **Live** tab showing the scanner's real-time orientation and position (the MIRACO streams pose and IMU data over WiFi).
- Wireless transfer over WiFi, so you don't need the USB cable.
- Confirm the base MIRACO and MIRACO Plus, and document any differences from the Pro.

## Privacy

Everything happens locally over USB. Nothing is uploaded anywhere.

## Contributing

Issues and pull requests are welcome, especially test reports from MIRACO Pro and Plus owners.

## License

MIT. See [LICENSE](LICENSE).

## Built with

`jmtpfs` and `libmtp` (device access), `rsync` (transfer), CustomTkinter and Pillow (UI), and `trimesh` (STL/OBJ/GLB export). PointYoink contains no code from other Revopoint tools - the MIRACO project layout was worked out directly.

## Related projects

Different scanners or different problems, listed so you can find the right tool if PointYoink isn't the fit:

- [HazenBabcock/revopoint-python](https://github.com/HazenBabcock/revopoint-python) - live control and capture of older **tethered** scanners (POP/MINI) over Wi-Fi.
- [ifilipis/metrox](https://github.com/ifilipis/metrox) - reverse-engineering and raw-frame processing for the **MetroX**, from PC-cached project files.
- [frostworx/revopoint-pop2-linux-info](https://github.com/frostworx/revopoint-pop2-linux-info) - notes on running a **POP2** on Linux (Wine/SSH).

---

Keywords: revopoint linux, revo scan linux, miraco linux, revopoint miraco linux, miraco pro linux, transfer miraco scans to linux, get files off miraco linux, revopoint mtp linux, revopoint ply export linux, revopoint no linux version, 3d scanner linux.
