# Audion Get Tools

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
