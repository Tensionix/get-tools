# Audion Get Tools

<!-- audion:release -->
<p align="center">
  <a href="https://audion.dev/downloads/get-tools"><img alt="Windows" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0b6db8?style=flat-square&logo=windows&logoColor=white"></a>
  <a href="https://github.com/Tensionix/get-tools/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/Tensionix/get-tools?style=flat-square&label=release&color=e08a63"></a>
  <a href="https://github.com/Tensionix/get-tools/releases"><img alt="Downloads" src="https://img.shields.io/github/downloads/Tensionix/get-tools/total?style=flat-square&label=downloads&color=5fd08a"></a>
  <a href="https://github.com/Tensionix/get-tools/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/Tensionix/get-tools?style=flat-square&color=5fd08a&logo=apache&logoColor=white&cacheSeconds=3600"></a>
</p>

**Version 2.15.0** · 2026-09-04 · 208.4 MB

- [Direct download](https://dl.audion.dev/get-tools/2.15.0/Audion_Get_Tools_v2.15.0_Full.zip) — unmetered, no rate limits
- [Project page](https://audion.dev/downloads/get-tools) — every version and how to install

<p align="center"><img src="docs/screenshot.png" alt="The program window" width="560"></p>

`SHA-256: 49719adb1adaa8e7b5d80dcc52870adf6b1c9149de2fc46bd0ca06c77c387c07`

---

An **Audion** tool, published by [Tensionix](https://github.com/Tensionix).
<!-- /audion:release -->


[Русский](README_RU.md) · [User Guide](USER_GUIDE_EN.md)

Installing, updating, checking, exporting, and importing sets of programs through
the Windows package manager.

## Why It Exists

A new machine means half a day of installing programs one by one. Reinstalling
the system means the same again, and half the programs are remembered only later,
when they turn out to be needed.

Windows can install packages by itself, from a command. But the list of what to
install still has to be kept in your head.

This program keeps it for you: package sets live as lists, and a machine is
brought up with one command.

## Principles

**A set is a list of names.** An ordinary text file where a package is added as a
line. No proprietary format, no database of its own.

**Export and import.** The list is taken from a configured machine and applied to
a new one.

**Checking is separate from installing.** First you see what will be installed and
what is missing from the source — then it installs.

## Next

* [User Guide](USER_GUIDE_EN.md) — step by step.

---

## Technical Reference

### Running

```cmd
Launcher-Audion-Get.cmd      command line
Launcher-Audion-Get-RU.cmd   the same in Russian
launcher_gui.cmd             windowed
```

### Sets

Package lists live in the configuration as separate files — one per machine
purpose.
