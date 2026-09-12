# PointYoink

[![Release](https://img.shields.io/github/v/release/datboip/pointyoink?color=4aa3ff)](https://github.com/datboip/pointyoink/releases)
[![Downloads](https://img.shields.io/github/downloads/datboip/pointyoink/total?color=3ecf8e)](https://github.com/datboip/pointyoink/releases)
[![Stars](https://img.shields.io/github/stars/datboip/pointyoink?color=ffb454)](https://github.com/datboip/pointyoink/stargazers)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Linux-informational)

**Get your Revopoint MIRACO 3D scans onto Linux, without Revo Scan, Wine, or a Windows VM.**

Revopoint's Revo Scan software is Windows, macOS, iOS and Android only. There is no Linux version, so getting your finished scans off a MIRACO on a Linux machine usually means dual-booting, running a Windows VM, or fighting a raw MTP copy that drags thousands of tiny files across a slow connection.

PointYoink is a small desktop app that does it directly, over USB or WiFi, and then takes the scans the rest of the way: build a model from raw frames on your graphics card, cut the table off, line up the sides you scanned separately and fuse them into one model, clean it up, and export STL, OBJ, GLB or PLY with a mesh check. Originals are never changed; every step saves a new version.

> Unofficial. Not affiliated with or endorsed by Revopoint. "Revopoint" and "MIRACO" are trademarks of their respective owners. PointYoink only reads files off your own device and contains none of Revopoint's software.

## Screenshots

<p align="center">
  <img src="images/splash.png" alt="PointYoink" width="440">
</p>

<p align="center">
  <img src="images/app_connected.png" alt="PointYoink main window - connected, with projects and a scan preview" width="920">
</p>

## What it does

Two pages. **Import** is the scanner: what is on it, the import options, one Import button. **Projects** is this PC: everything you imported, a 3D view you can turn, and a NEXT bar that says what to do now and does it with one button.

- **Import over USB** (File Transfer mode lists every project) or **over WiFi** (the scanner's Share to PC sends one project to a 4-digit code PointYoink shows you; about 20 MB/s, faster than the cable). Finished models is quick; Full project also brings the raw frames.
- **Build**: turn a scan's raw frames into a 3D model on your PC, using the scanner's own registration. Seconds on an NVIDIA card, minutes on a CPU. Measured against the scanner's One-tap Edit: 0.2 mm median.
- **Cut base**: drag one line above the table in the 3D view; grey stays, red goes. The cut is remembered per scan and applied again when scans are combined, so the table never gets fused in.
- **Combine**: scanned each side separately? Click three to five matching spots on two scans, or press Auto, check the orange overlay, keep it, repeat for each side, then build one model from all the frames at once. Your points stay editable.
- **Prepare**: remove floating pieces, smooth, fill small holes, reduce triangles, with the scanner's defaults. Before and after side by side, Keep or Discard.
- **Export**: version, format and folder together, with the model's size in mm, triangle count, pieces and open edges shown first.
- Badges tell you what the scanner already did (raw only, partly scanner-edited, scanner-edited) and what you made (combined, prepared). Compare any two versions in linked 3D views.
- Rename projects, remember what was imported, export a project as one ZIP, cancel and retry, a built-in error log, a dark UI, and a first-run "How this works" panel.

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

1. **Import.** Plug in a USB-C data cable and tap **File Transfer** on the scanner, or click **WiFi** and enter the code on the scanner under Share to PC > Wi-Fi. Tick, Import. Choose **Full project** if you want to build or combine on the PC.
2. **Projects.** The imported project appears with its scans. Follow the **NEXT** bar: it walks you through the five steps below and each one is a single button.

| Step | What happens | Where the result goes |
|---|---|---|
| Build | raw frames become a 3D model (One-tap Edit on the scanner does this too; build here when that did not turn out right) | `<project>_<scan>_pcfused.ply` |
| Cut base | one line above the table, Apply; the plane is remembered | `<project>_<scan>_clean.ply` |
| Combine | line up the sides on matching points, then fuse all their frames into one model, table already dropped | `<project>_combined_pcfused.ply` |
| Prepare | floating pieces, smoothing, holes, triangle count; before and after | `<project>_<scan>_clean.ply` |
| Export | STL, OBJ, GLB or PLY with a size and mesh check | the folder you choose |

Every step saves a new version and you pick which one the preview and exports use, so an original from the scanner is never overwritten.

## How it works

A MIRACO project holds thousands of raw depth frames plus the finished, fused output. **Finished models** copies just the finished output, so an import moves megabytes instead of gigabytes. **Full project** keeps the scanner's nested layout with the raw frames, which is what Build and Combine read.

Build fuses the frames in a TSDF volume ([Open3D](https://www.open3d.org/), GPU when there is one) using the globally registered poses the scanner writes with every scan (`cache/global_register_pose.pose`). That table is what makes multi-pass scans line up the way they do on the device: on three test scans the result sits 0.2 mm median from the scanner's own One-tap Edit model. Combine solves a rigid fit from your point pairs (or from FPFH features with Auto), refines it with ICP, and then fuses every scan's frames into one volume with each scan's base plane culled first. All the heavy work runs in memory-capped subprocesses so a huge mesh can never take the machine down.

Imported files land in a flat layout with unique names:

```
revopoint-scans-models/
  Project09102026033917/
    Project09102026033917_<scan>.ply           # the scanner's finished model, one per scan
    Project09102026033917_<scan>_pcfused.ply   # a model built on this PC
    Project09102026033917_<scan>_clean.ply     # the prepared or base-cut version
    Project09102026033917_combined_pcfused.ply # one model from all the scans you lined up
    Project09102026033917_<scan>.png           # the scan's preview render
    data/<scan>/...                            # raw frames and the scanner's own files (Full project)
```

**Export ZIP** bundles the selected projects with clean flat names.

## Troubleshooting

- **Nothing detected:** make sure you tapped File Transfer on the scanner, not just plugged it in. Try a different USB-C cable, since some only charge.
- **Connect fails or hangs:** unplug and replug, tap File Transfer again. PointYoink clears stale connections on its own.
- **Previews are blank:** the scanner is still waking up. Click the project again.
- Anything else: open **Log** in the app, copy it, and file an issue.

## Roadmap

- Selection tools in the 3D view (lasso, brush) for removing bad regions, and smoothing only a selected area.
- An assembly view for parts that belong together but must stay separate.
- Presets and a job queue for repeat work.
- A **Live** view showing the scanner's real-time orientation (the MIRACO streams pose and IMU data over WiFi); the tethered Revopoint RANGE already streams into it.
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
