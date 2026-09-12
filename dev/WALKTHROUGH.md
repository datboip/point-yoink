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

## When you want more: the five steps on the Projects page

After an import, open **Projects**. The NEXT bar under the project title says what to do now and does it
with one button; the trail shows Build → Cut base → Combine → Prepare → Export.

1. **Build** (only for scans that are raw frames). One-tap Edit on the scanner does the same job and is
   the easy path; build here when that did not turn out right, or when you want to combine sides.
   Needs the raw frames (Full project) and Open3D. Seconds on an NVIDIA card.
2. **Cut base**. Remove base on each scan: the scan in the 3D view, grey stays, red goes, one slider,
   Flip if it chose the wrong side, Apply. The cut is remembered for that scan and applied again when
   scans are combined, so the table never gets fused into the combined model.
3. **Combine**. Scanned each side separately? Combine scans: pick the base scan, click three to five
   matching spots on it and on another scan, Line up from points (or Auto when they overlap a lot),
   check the orange overlay, Keep. Repeat for each side, then Build one model. Reopen a lined-up scan
   any time to add or undo points.
4. **Prepare**. Remove floating pieces, smooth, fill small holes, reduce triangles, with the scanner's
   defaults. Before and after side by side; Keep or Discard. Once Combined exists, prepare that one.
5. **Export**. Version, format (STL for slicers, OBJ, GLB, PLY) and folder together; the size in mm and
   a mesh check (pieces, open edges) show first.

Every step saves a new version. The version chips on the right pick which one the preview and exports
use; the original from the scanner is never overwritten. Compare versions puts any two side by side.

## If something does not work

- WiFi finds nothing after 30 seconds: both must be on the same network; allow port 9706 (UDP and
  TCP) in your firewall; click **New code** and try again.
- The scanner says "Transfer failed" right away: PointYoink was not listening (open the WiFi card
  before you type the code).
- USB shows "Not connected": tap **File Transfer** on the scanner, then click **USB** again.
- "Imported" badge but no files: the badge only appears when a real model landed; re-import.

## Screenshots and recordings

They only come over the USB cable (Captures tab). WiFi sends the project you share and nothing
else; the scanner has no way to offer its screenshots over the network.
