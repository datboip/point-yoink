# Third-party notices

PointYoink is MIT licensed (see LICENSE). It stands on the following open-source work.

## Bundled in the Debian package

The `.deb` ships these Python packages inside `/usr/lib/point-yoink/vendor/` so it works without pip. Each one's full license text is kept alongside it in its `*.dist-info` folder.

| Package | Version | License | Author / project |
|---|---|---|---|
| customtkinter | 6.0.0 | Creative Commons Zero v1.0 Universal | Tom Schimansky ([https://customtkinter.tomschimansky.com](https://customtkinter.tomschimansky.com)) |
| darkdetect | 0.8.0 | BSD-3-Clause | Alberto Sottile ([http://github.com/albertosottile/darkdetect](http://github.com/albertosottile/darkdetect)) |
| fast_simplification | 0.2.0 | MIT | Alex Kaszynski ([https://github.com/pyvista/fast-simplification](https://github.com/pyvista/fast-simplification)) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | Donald Stufft ([https://github.com/pypa/packaging](https://github.com/pypa/packaging)) |
| pyglet | 1.5.31 | BSD | Alex Holkner ([http://pyglet.readthedocs.org/en/latest/](http://pyglet.readthedocs.org/en/latest/)) |
| PyOpenGL | 3.1.10 | BSD License | Mike C. Fletcher ([https://mcfletch.github.io/pyopengl/](https://mcfletch.github.io/pyopengl/)) |
| pyopengltk | 0.0.4 | MIT | Jon Wright ([http://github.com/jonwright/pyopengltk](http://github.com/jonwright/pyopengltk)) |
| trimesh | 5.1.0 | MIT License | Michael Dawson-Haggerty ([https://github.com/mikedh/trimesh](https://github.com/mikedh/trimesh)) |

## Used at runtime, installed from your distribution

Not bundled; pulled in as package dependencies: Python 3, Tk (`python3-tk`), Pillow (`python3-pil`, `python3-pil.imagetk`), NumPy, Matplotlib, NetworkX, `jmtpfs` / `libmtp` (MTP access to the scanner), `rsync`, `xdg-utils`, FUSE. Optional: Open3D (`pip install open3d`) for building and combining models on the PC.

## Fonts

The wordmark uses the Ubuntu font when it is installed on your system (Ubuntu Font Licence). Nothing is bundled.

## Scanner screenshots

The in-app guide (How this works) shows screenshots of the MIRACO's own on-device screens, captured from the author's scanner, to show where each setting lives. Revopoint and MIRACO are trademarks of their respective owners. PointYoink is an independent project, not affiliated with or endorsed by Revopoint, and contains no Revopoint software.

## Icons

The line icons in `assets/icons/` are PointYoink's own set (see `assets/icons/manifest.json`).
