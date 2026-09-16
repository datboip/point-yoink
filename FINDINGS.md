# PointYoink sanity audit — 2026-09-16 (0.9.86-pre)

Three read-only audits (hardcoded values, misleading UI text, flow correctness). Line numbers
were accurate at 0.9.86-pre `910e6e5`; verify before fixing. Ranked; ✅ = fixed in 0.9.87-pre.

## Correctness / data-loss (fix first)

1. **Stale strip thumbnail after version switch/edit.** The big preview key includes the version
   (`_shade` at ~2848), but the film-strip thumbnail is keyed only `"%s__%s__film.png" % (name,node)`
   — no version — and `_scan_thumb` returns it on existence alone, no mtime check. Switching current
   version (scanner↔prepared↔pcfused) or editing the mesh leaves the strip showing the OLD version
   while the big preview shows the new one. `render_gallery` only re-renders when NO film PNG exists.
   Fix: add verkey to the film key + an mtime-vs-mesh check; delete `__film.png` on edit/version-change.

2. **Restore of a past prepared version shows the WRONG model.** `_prep_restore` uses
   `shutil.copy2` (~4118) which preserves the archive's OLD mtime; verkey stays "clean" so the PNG
   cache path is unchanged, and `_request_shaded`'s freshness test `getmtime(out) >= getmtime(mesh)`
   (~2858) passes on the stale PNG → previous version shown. Same mtime hazard touches the new
   mesh cache (`shade._mesh_key`). Fix: `os.utime(final, None)` (bump mtime to now) after restore.

3. **Rebuilding combined/pcfused over an existing model is non-atomic.** `fuse.py` writes straight
   to `--out` (no tmp+replace). An OOM-kill/crash mid-write truncates the previous good build. Prepare
   is protected (backs up `_clean.ply` to `.versions/`); pcfused/combined are not. Fix: write to a
   temp then `os.replace`, like Prepare/shade.

4. **Cancelled partial import later reads as "already imported, unchanged."** `_import_full` cancel
   leaves partially-rsynced files; `is_imported()` sees a >1KB .ply (True) and `changed()` returns
   False (no imported_at) → offered as complete. Fix: mark/clean partial imports, or gate is_imported
   on a completion marker.

5. **Successful imports in a partly-failed batch never recorded.** `_finish` records imported_at/sig
   only in the `else` branch; if any project failed and the user declines retry, the `elif failed`
   block ends and successful siblings get no record → never show imported, `changed()` blind. Fix:
   record per-project success regardless of batch outcome.

6. **A single transient render failure disables that scan's preview all session.** `_shade_failed`
   is only added, never cleared; re-preparing keeps verkey "clean" so even a rebuild won't clear it —
   "could not draw the 3D model" until restart. Fix: clear on success / on rebuild.

7. **WiFi keep-and-add mutates the live project in place, non-atomically** (~5440). An exception
   between moving scans and copying files leaves the project half-merged. Fix: stage + atomic swap.

8. **Build button can stick disabled for the session.** `_fuse_worker` doesn't emit `fuse_done` in a
   `finally`; an exception outside its inner try (e.g. `os.makedirs` ~4705) skips it, `_fusing` stays
   True → "a build is already running" forever. `_pull_worker` does this right (try/finally); mirror it.

## Misleading UI text / displayed state

9. ✅ **"3D model · N triangles" lingers on a raw scan.** `renders_lbl` was only cleared on project
   switch, not scan switch, and a late `mesh_stats` for the previous scan re-stamped it. Fixed: clear
   `renders_lbl` + reset `_shade_key` in the no-mesh branch, and gate the "View in 3D" hint on mesh
   existence.
10. **Stale bottom-bar status.** Several `set_status(...)` never clear and sit as the permanent
    Ready-replacement: "Reset view works once…" (~2766), "3D model(s) built:…" (~5986), "Scanner
    found at…" (~6000). Fix: auto-clear after a few seconds or on the next state change.
11. **Generic loading strings the user dislikes.** Bare "Loading…"/"Working…"/"loading…" at 4216,
    4254, 4315/4318, 5522, 4560; vague "Loading the 3D view…" ×4 (3357/4425/4469/5348). Good models to
    copy: "Loading screenshot %d/%d…", "Preloading previews… %d / %d". Give each a noun + progress.
12. **False attribution:** "Size … mm (as measured by the scanner)" (~4095) — the extent is measured
    locally by `process.py`, not the scanner. Drop the attribution.

## Hardcoded / fragile

13. **Launcher assumes `~/pointyoink`** (`RunPointYoink.sh`). Derive dir from the script's own path.
14. **MTP folder names hardcoded** ("Internal shared storage"/"Projects"/"Screenshots", ~67) — a
    firmware/locale rename breaks discovery with no clear error.
15. **`range.py` DEVICES table** hardcodes MIRACO PC-mode PID→resolution (looks like a guess;
    `pcmode_probe.py` exists to discover it). Wrong PID → "no scanner cameras on USB".
16. **`POINTYOINK_MEM_CAP_GB` has 5 different defaults** for one concept (cutplane 10, process 10,
    register 12, fuse 12, align 8) and is re-hardcoded at 6 call sites in pointyoink.py. Unify.
17. **`LIVE_QUALITY_FACES` medium (300000) re-hardcoded** as a literal fallback at 3069/3036; the
    600000 cut-plane cap is copy-pasted 7×. Single-source these constants.
18. **Hardcoded "the scanner uses X%" claims** in the Prepare dialog (~4180-4188) presented as the
    scanner's actual behavior — unverifiable across firmware; soften to "typical" or drop.

## Confirmed SOUND (no action)
- `_proc_current` self-heals a stale version pointer (falls back to a real file).
- Worker threads consistently marshal Tk via `self.q` — no off-thread Tk mutation found.
- shade.py preview writes are atomic (tmp + os.replace); Prepare backs up before overwrite and aborts
  if the backup fails.
