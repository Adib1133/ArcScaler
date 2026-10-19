# ArcScaler

A native Windows desktop manager for per-game OptiScaler and Intel XeSS configuration.

## Run

Launch **ArcScaler.exe**. The app stores profiles, downloaded releases, cached game icons and logs in `%LOCALAPPDATA%\ArcScaler`.

Existing `%LOCALAPPDATA%\OptiScalerXeSS` and `%APPDATA%\OptiScalerXeSS` data is copied on first launch. Existing ArcScaler records win conflicts; the conflicting originals remain in `migration-originals`, and legacy folders are never deleted. Super Resolution and Frame Generation can be enabled together.

For development, install `requirements.txt` and run `python main.py`. Set `ARCSCALER_DATA_DIR` to isolate development or tests. The legacy environment variable is accepted for migration compatibility.

## Library

Add an executable, Windows shortcut or game folder, or scan Steam and Epic libraries. Select a game in the middle rail to edit it. Icons are extracted from the real executable in background workers and cached on disk; games without resources receive a name-based placeholder.

- **Choose setup** offers native-XeSS-with-added-FG, DLSS-to-XeSS upscaling, native-runtime reuse for both, and advanced configuration. A route summary shows the inputs, outputs, file-action counts and in-game steps. **Super Resolution** and **Frame Generation** remain independent; inactive sections collapse. Both may be off, which disables installation.
- **Suggested settings** expands on demand. Suggestions preserve deliberately selected native-runtime methods, inputs and paths. Applying a suggestion changes only the saved draft. **Changes not installed**, **Update installation**, and **Up to date** distinguish the draft from the installed configuration.
- **Advanced settings** expands install mode, release, proxy, network model and overrides. Hardware selection is automatic: the XeSS runtime chooses XMX or DP4a; the network control selects a model variant.
- **Install to game** shows a compact table with Add, Replace, Reuse, Restore and Remove actions, full paths and reasons. Confirmation applies that plan only if its settings, dependencies, package hashes and destination hashes still match.
- **Force inject**, under **Advanced settings** or **More**, lists the compatibility conflicts it will override and requires a separate acknowledgement. Changed managed files are preserved separately before replacement. It cannot bypass malformed files, missing native APIs, path containment, running-game protection, corrupt backups or the DirectX 12 requirement for Frame Generation.
- **Undo last change**, under **Advanced settings** or **More**, restores the previous deployment after an update, including its previous proxy and configuration. One checkpoint is retained; older checkpoint blobs are pruned after successful installation. Undo validates every saved file before writing and can retry after interruption. External edits block undo. Original-game backups remain independent. For the first installation, use **Restore original files**.
- **Restore original files** restores original bytes and removes files ArcScaler created. Changed files are blocked from replacement until the user chooses to preserve separate copies. Preserved copies and an index remain in `.optiscaler_backup/preserved`. Recovery uses the original snapshot across updates.
- **Launch** observes startup failures; verified installed files are not proof of runtime activation. Check the game's overlay.
- **More** contains folder access, rescanning, verification, restoration, diagnostics, force injection and library removal.
- **Runtime evidence** distinguishes game/unmanaged files, ArcScaler files and original backups. Generic FSR/FidelityFX files indicate uncertain FG support, not confirmed FG support. File health, a default `OptiScaler.log` updated since installation, and user confirmation from the overlay are separate states. Log text never automatically confirms an active route. Overlay confirmation applies only to the installed configuration and is invalidated by reinstall/undo; changed files prevent confirmation.
- **Reuse native XeSS** offers two DirectX 12 workflows: **Reuse native XeSS + FG** selects DLSS/DLSSG inputs and reuses the game's XeSS, XeFG and XeLL libraries; **Keep native XeSS; add missing FG** leaves upscaling native and installs only missing FG dependencies plus the OptiScaler proxy, INI and required FakeNvapi helpers. Turn off Frame Generation in the first workflow for native SR reuse alone. Blank DLL paths use detected files; browse to resolve missing/ambiguous locations within the game installation. The preview lists every reused and written file. PE structure and hashes are checked, but runtime API compatibility must be verified in game. Native XeSS FG should be disabled while testing the DLSSG-to-XeFG route. Restore original files leaves reused libraries untouched. Restore an older bundled installation before attempting to reuse its managed DLLs as native files.
- Per-game expansion and scroll state are retained. Narrow windows use compact navigation. **App settings** provides 100–200% interface sizing and reduced motion.

Installation stages are journaled before deployment and verification. Startup identifies interrupted operations, missing files, externally modified files and damaged backups. Choose recovery before installing again; incomplete recovery retains the backup.

## Automatic decisions

Auto mode resolves shipping executables, inspects PE imports and local graphics modules, detects native upscaler/FG runtimes, and selects a collision-free proxy. Local compatibility entries take precedence, followed by imported utility DLLs and generic fallbacks. Existing proxies are preserved; load success still requires verification in the game. Unknown or ambiguous APIs do not enable FG. Anti-cheat detection blocks Auto mode.

`assets/compatibility.json` contains local entries keyed by Steam appid and executable name. Use **App settings → Edit compatibility overrides** to create `%LOCALAPPDATA%\ArcScaler\compatibility.json`:

```json
{"steam":{},"exe":{"example.exe":{"hook":"winmm.dll","input":"DLSS","notes":"User-reviewed offline setup","ini_overrides":{"InitFlags":{"DepthInverted":"true"}}}}}
```

Overrides permit depth, jitter and spoofing values. Invalid entries stop planning. DLL presence provides evidence, not proof of a game's active rendering API. Unsupported or ambiguous games require manual verification.

Deployment discovers supported XeSS/FakeNvapi runtime variants from the selected release. It excludes unrelated upscalers, scripts, executables and optional runtime folders. Required dependencies, PE structure and available package hashes are checked before mutation. OptiScaler's upstream filenames and legacy recovery markers remain unchanged for compatibility.

Native dependencies are resolved before bundled requirements, so a reused runtime need not exist in the package. Native validation reads AMD64 PE structure, core API exports and available Windows file-version resources without executing the DLL. Missing exports block reuse even in force mode. An unknown version is reported explicitly; no unsupported minimum-version guarantee is invented. Passing these checks is not proof of compatibility with a particular game, driver or SDK feature.

Tk cyclic garbage collection runs periodically on the UI thread to keep font/image finalization out of background operations. Automatic collection is restored when the last window closes.

## Other screens

**Downloads:** browse official release metadata, filter downloaded releases, download, validate, select a release, delete it, or open the package folder. Downloads can be cancelled before commit; the previous release is retained. Existing checksum mismatches are reported rather than silently trusted.

**Hardware:** re-detect the GPU, copy diagnostics, and apply recommended settings to the selected game.

**Log:** filter all activity, warnings or errors, then copy or clear the visible log.

**App settings:** open app data, edit local compatibility overrides or preview a diagnostic report. Reports include evidence, effective settings, health and recent activity, with personal paths redacted by default. Nothing is uploaded. Preferences and per-game drafts are saved automatically.

## Build and test

ArcScaler application source is licensed under GPL-2.0-only; see `LICENSE.txt`.
Third-party runtimes retain their own licenses (see `THIRD_PARTY_NOTICES.txt`).

### Windows installer

After `python build.py`, run `python build_installer.py --iscc "path/to/ISCC.exe"`
with Inno Setup 6.6 or newer. Output: `dist/ArcScaler-Setup.exe`. Installation is
per-user, with a dark wizard, GPL v2 license page, optional desktop shortcut,
Start-menu entry and Windows uninstall registration. Uninstall preserves user
profiles and downloaded runtimes in LocalAppData/ArcScaler.

Default builds are **unsigned**. With a trusted code-signing certificate and its
private key accessible in CurrentUser/My, use:

```
python build_installer.py --certificate CERTIFICATE_THUMBPRINT --signtool "path/to/signtool.exe" --timestamp-url https://YOUR_RFC3161_SERVICE
```

This signs the application, installer and uninstaller using SHA-256, timestamps
signatures, and requires Windows signature verification to pass. No certificate
or private key is created, embedded or installed by these scripts.

The installed `ArcScaler-source.zip` contains application source, tests, artwork
and build scripts. To rebuild, install `requirements-build.txt` and copy the
unmodified `LocalAppData/ArcScaler/assets/versions/v0.9.4` runtime directory to
`assets/versions/v0.9.4` in the extracted source tree. Those third-party binaries
are preserved separately from the GPL application source.

Install `requirements-build.txt`, then run `python build.py`. This creates `dist/ArcScaler.exe` with the supplied icon at all seven Windows sizes and ArcScaler version metadata. `ArcScaler.iss` is an optional Inno Setup 6 installer recipe using the same icon. No installer compiler was bundled with this project.

The executable keeps the complete offline v0.9.4 runtime package in a lossless LZMA bundle. Identical files share one compressed payload and are restored under their original names. First launch extracts into a staging directory, verifies SHA-256 hashes and package validity, then commits the release. Existing downloaded releases are preserved. NumPy, unused AVIF support, and py7zr are excluded; release extraction continues to use Windows tar for 7z and Python zipfile for ZIP. Run `python audit/smoke_release.py` to verify every restored file against the source package.

Run `python audit/run_checks.py` for isolated tests. The test executables are structural PE fixtures and are never executed as games.

Run `python audit/release_gate.py` to run regressions, rebuild, and smoke-test the distributed executable with a temporary data directory. Regression coverage includes process termination at transaction stages, changed-file preservation, corrupt backups, cancelled downloads, stale task results, combined SR+FG, and control layout at 100–200% scale.

### Source repository contents

The source repository excludes generated executables, installers, runtime DLLs,
local logs, caches, signing keys and generated Graphify output. Distribute built
executables and installers through GitHub Releases. The small upstream INI
fixture remains under `assets/versions/v0.9.4` so tests work without runtime DLLs.
Development launches can download runtimes through the app's Downloads tab.
Before building an offline executable, copy the complete validated v0.9.4
package from `%LOCALAPPDATA%/ArcScaler/assets/versions/v0.9.4` into the matching
repository directory. Never commit the downloaded binaries or personal profiles.

Run `graphify update .` locally to regenerate the code knowledge graph.

## Website and uploading

The `website/` folder contains the static ArcScaler showcase with the app icon and actual UI captures. See `website/README.md` for local preview and the manual GitHub Pages workflow.

Upload the **contents** of this source folder as the repository root. Keep source, tests, build scripts, license notices, artwork, the INI test fixture and the website. Attach generated EXE installers to GitHub Releases separately. No runtime DLLs, build outputs, personal profiles, cached releases or local audit reports belong in source control.

Test prerequisites: the optional 7z extraction test needs `py7zr`; the real-runtime export test needs the complete local v0.9.4 package (excluded from Git). GUI integration tests require a working Windows Tk environment. For isolated checks, set `ARCSCALER_DATA_DIR` to a disposable writable directory and use `python -X utf8 audit/run_checks.py`.
