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

The PowerShell wrapper resolves PowerShell in this order: the program's own `App\AudionPs.exe` (when the window is built), `pwsh.exe` from PATH, then built-in `powershell.exe`.

## Current Builder Order And Dependency Hygiene

`builder_main.cmd` uses fixed numeric entries. Keep the bootstrap order stable: `[01] PYTHON ENV CMD`, `[02] PYTHON ENV PS`, `[03] FZF`, then project-specific payload installers and one-time maintenance/diagnostic actions below.

Current builder install/maintenance map:

```text
[01] PYTHON ENV CMD
[02] PYTHON ENV PS
[03] FZF
[06] WINDOW
[70] CLEAN INSTALL CACHE
[71] VERIFY / DOCTOR
[72] CMD ENCODING CHECK
[90] PROJECT LAUNCHER
[95] OPEN install
[96] OPEN runtime
[97] OPEN wheelhouse
[98] OPEN config
[99] OPEN licenses
[100] OPEN release
[00] EXIT
```

Project-specific payload entries before diagnostics:

No project-specific external payload installer before diagnostics.

Dependency hygiene rules:

- Python Embedded tracks the latest `3.12.x`; do not pin a concrete patch version in docs or scripts.
- Use the active embedded Python `_pth` file for path edits; do not hard-code a concrete filename.
- Bootstrap installs must include `setuptools`, `wheel`, and `packaging` before building or installing project wheels.
- `runtime\`, `wheelhouse\`, and `system_core\fzf.exe` are reproducible payloads.
- `config\window_settings.json`, `config\terminal_commands.json` and `config\gui_*_cache.json` are the state of one machine's window (its look and folders, the history of the terminal line, cached lists): not in git, removed by `cleanup_project.cmd` from a copy made for a release. Install/update scripts may cleanly replace only their owned targets.
- GPL or unknown-license external tools are explicit install/update payloads. Prefer GUI install buttons where the project exposes them, or fixed builder entries otherwise; do not silently bundle them as default source contents.
- `install\Clean-Install-Cache.cmd` / `.ps1` is the general install-cache cleanup. It removes transient `install\download\` artifacts (preserving `get-pip.py`), the installer staging dir `system_core\_fzf_tmp`, what building `App\` leaves in `bin` and `obj` of `Source\` and `Source\Ps\`, and Python bytecode caches outside runtime, wheelhouse, and user-data zones. The built window in `App\` stays.
- `cleanup_project.cmd` is a separate source/release cleanup tool. It can remove runtime payloads and user-output zones after explicit confirmation, and it leaves `App\` alone; do not describe it as the general install-cache cleaner and do not wire it into install flow.

Project-specific notes:

- WinGet package actions are user-facing package installs, not bundled source contents. Keep installer UI visible and keep bulk install/update/pin flows under explicit confirmation.

## Portable Python Flow

The core environment build creates managed folders, resolves the latest Python Embedded `3.12.x`, extracts it to `runtime\`, enables `import site` in `python3<minor>._pth`, downloads `get-pip.py`, installs `setuptools` / `wheel` / `packaging`, rebuilds `wheelhouse\`, installs the packages of `install\requirements.in` from the local wheelhouse, and verifies with `system_core\doctor.py`. The list holds what the engine runs on; the window is not built from it.

Verify with:

```bat
install\verify_portable_env.cmd
```

It checks the environment (`system_core\doctor.py`), the console command (`get_cli.cmd describe`) and the built window: `App\AudionGetTools.exe` checks itself against the manifest of this version. Nothing in it needs the .NET SDK.

## The Window

The window is `App\AudionGetTools.exe`, built from `Source\` (C#, Avalonia) with its own .NET runtime: a machine that runs the program needs nothing installed. Beside it stands `App\AudionPs.exe` - PowerShell 7 under the program's name (`Source\Ps`, Microsoft.PowerShell.SDK), sharing that runtime: the console menus of WinGet, the command line of the terminal and the scripts of the builder run with it, so no PowerShell of the machine decides how they behave. `Start.exe` in the root starts it.

```bat
install\Build-App.cmd
install\Build-StartLauncher.cmd
```

`install\Build-App.cmd` is builder step `[06] WINDOW` and needs the .NET 10 SDK. It runs the tests of the console command and of the dictionary of the window, publishes into `._runtime\app_stage`, lets the built window check itself, and only then replaces `App\`; a failed build leaves the working copy in place. The steps of a release update what the engine runs on and do not rebuild `App\`: run step `[06]` after a change in `Source\`.

`install\Build-StartLauncher.cmd` compiles `Start.exe` with the C# compiler of .NET Framework and embeds `system_core\icons\app.ico`. The launcher asks for administrator rights once; a "no" opens the window without them.

## Release Licensing

Third-party notices and license files are generated from the finalized staged release contents during release packaging. They are not generated during routine environment build/install steps.


