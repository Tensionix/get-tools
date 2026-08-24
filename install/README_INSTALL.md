# Audion Get Tools - install notes

## Main Build Paths

Recommended entry point:

```bat
builder_main.cmd
```

Direct CMD Python environment build:

```bat
install\Build_Portable_Env_Build.cmd
```

Optional PowerShell route:

```bat
install\Build_Portable_Env.cmd
```

The PowerShell wrapper resolves PowerShell in this order: portable `system_core\powershell\pwsh.exe`, `pwsh.exe` from PATH, then built-in `powershell.exe`.

## Current Builder Order And Dependency Hygiene

`builder_main.cmd` uses fixed numeric entries. Keep the bootstrap order stable: `[01] PYTHON ENV CMD`, `[02] PYTHON ENV PS`, `[03] FZF`, `[04] POWERSHELL`, then project-specific payload installers and one-time maintenance/diagnostic actions below.

Current builder install/maintenance map:

```text
[01] PYTHON ENV CMD
[02] PYTHON ENV PS
[03] FZF
[04] POWERSHELL
[70] CLEAN INSTALL CACHE
[71] VERIFY / DOCTOR
[72] CMD ENCODING CHECK
[77] MAKE RELEASE ARCHIVE
[90] PROJECT LAUNCHER
[93] GUI LAUNCHER
[95] OPEN install
[96] OPEN runtime
[97] OPEN wheelhouse
[98] OPEN config
[99] OPEN licenses
[00] EXIT
```

Project-specific payload entries before diagnostics:

No project-specific external payload installer before diagnostics.

Dependency hygiene rules:

- Python Embedded tracks the latest `3.12.x`; do not pin a concrete patch version in docs or scripts.
- Use the active embedded Python `_pth` file for path edits; do not hard-code a concrete filename.
- Bootstrap installs must include `setuptools`, `wheel`, and `packaging` before building or installing project wheels.
- `runtime\`, `wheelhouse\`, `system_core\powershell\`, `system_core\fzf.exe`, browser payloads, and external tool folders are reproducible payloads. Install/update scripts may cleanly replace only their owned targets.
- GPL or unknown-license external tools are explicit install/update payloads. Prefer GUI install buttons where the project exposes them, or fixed builder entries otherwise; do not silently bundle them as default source contents.
- `install\Clean-Install-Cache.cmd` / `.ps1` is the general install-cache cleanup. It removes transient `install\download\` artifacts (preserving `.gitkeep`, `get-pip.py`, and `7z*-extra.7z`), exact installer staging dirs `system_core\_pwsh_tmp` / `system_core\_fzf_tmp`, and Python bytecode caches outside runtime, wheelhouse, and user-data zones.
- `cleanup_project.cmd` is a separate source/release cleanup tool. It can remove runtime payloads and user-output zones after explicit confirmation; do not describe it as the general install-cache cleaner and do not wire it into install flow.

Project-specific notes:

- WinGet package actions are user-facing package installs, not bundled source contents. Keep installer UI visible and keep bulk install/update/pin flows under explicit confirmation.

## Portable Python Flow

The core environment build creates managed folders, resolves the latest Python Embedded `3.12.x`, extracts it to `runtime\`, enables `import site` in `python3<minor>._pth`, downloads `get-pip.py`, installs `setuptools` / `wheel` / `packaging`, rebuilds `wheelhouse\`, installs from the local wheelhouse, and verifies with `system_core\doctor.py` plus GUI smoke where the project has a NiceGUI shell.

If `runtime\` and `wheelhouse\` are already populated, use the offline entry:

```bat
install\install_portable_offline.cmd
```

Then verify with:

```bat
install\verify_portable_env.cmd
```

## Release Licensing

Third-party notices and license files are generated from the finalized staged release contents during release packaging. They are not generated during routine environment build/install steps.


