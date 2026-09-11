# PointYoink walkthrough (draft for the in-app guide and the README)

Screenshots to drop in: [scanner-share-icon], [scanner-wifi-code], [scanner-finished], [scanner-usb-popup],
[app-wifi-card], [app-picker], [app-import-done]. Device shots come from the MIRACO's own screenshots
(Captures tab pulls them). App shots come from dev/render.py.

## The one-click path (what most people want)

1. **Get the project onto your PC.** Two ways; the difference matters:
   - **USB** shows everything on the scanner: every project, plus its screenshots and recordings.
     You tick what you want. Best for grabbing several projects at once.
   - **WiFi** sends only the one project you open Share from, and only that. Best when the cable
     is not handy or you want one project fast (it is quicker than the cable).
   - **WiFi**: click **WiFi** in PointYoink. A 4-digit code appears. On the MIRACO open the project,
     tap the share icon, choose **Wi-Fi**, type the code. The project comes across on its own
     (a 1 GB project takes under a minute). [scanner-share-icon] [scanner-wifi-code] [app-wifi-card]
   - **USB**: plug in the cable, tap **File Transfer** on the scanner's popup, click **USB**.
     Your projects appear in the list. [scanner-usb-popup]
2. **Import.** Tick the projects you want and click **Import**. The defaults do the right thing:
   finished models only (no raw frames), PLY kept, STL added for printing. [app-picker] [app-import-done]
3. **Done.** The folder opens (if you left that on). Each scan is a `.ply` and a `.stl`, named after
   the project and the scan, plus a preview image.

That is the whole workflow. Everything below is optional.

## When you want more

- **View in 3D**: rotate, zoom and pan the mesh right in the window; **Open 3D viewer** for the
  full-detail window.
- **Remove base**: slice the table or turntable off a scan with one slider; the original is kept.
- **Process on PC**: rebuild a scan's mesh from the raw frames on your own GPU, in seconds instead of
  minutes on the scanner. Needs the full project (untick "Models only" for that import).
- **Export ZIP**: bundle a project's models into one zip, with a size estimate first, for sharing.
- **Captures**: pull the scanner's screenshots and screen recordings.
- **Live view**: the MIRACO's live pose over WiFi, or a tethered Revopoint RANGE's cameras.

## If something does not work

- WiFi finds nothing after 30 seconds: both must be on the same network; allow port 9706 (UDP and
  TCP) in your firewall; click **New code** and try again.
- The scanner says "Transfer failed" right away: PointYoink was not listening (open the WiFi card
  before you type the code).
- USB shows "Not connected": tap **File Transfer** on the scanner, then click **USB** again.
- "Imported" badge but no files: the badge only appears when a real model landed; re-import.
