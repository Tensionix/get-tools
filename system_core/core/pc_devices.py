"""The devices of this PC: what is plugged in now, by its hardware id, with the driver that runs it.

A driver from the Microsoft Update Catalog is searched for by the hardware id of the very device it
is for, and a card of a list is ticked only when the PC has such a device. Audion Setup Tools asks
WMI for this (Win32_PnPEntity, Win32_PnPSignedDriver - seconds a query); here the same is read from
where Windows keeps it, at once and in any language of Windows:

  HKLM\\SYSTEM\\CurrentControlSet\\Enum\\<bus>\\<device>\\<instance>   the device: its name, its ids, its driver key
  HKLM\\SYSTEM\\CurrentControlSet\\Control\\Class\\<driver key>        the driver: its version

The registry remembers every device the PC has ever seen. Whether a device is here now, the
configuration manager tells (CM_Locate_DevNodeW finds only what is present).
"""
from __future__ import annotations

import ctypes
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

ENUM_KEY = r"SYSTEM\CurrentControlSet\Enum"
CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class"

# The buses a driver of the catalog is ever matched on.
BUSES = ("PCI", "USB", "HDAUDIO", "INTELAUDIO", "ACPI", "BTH", "SD")

CHIP_ID = re.compile(
    r"^[^\\]+\\(VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4}|FUNC_01&VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4}|VID_[0-9A-F]{4}&PID_[0-9A-F]{4})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Device:
    """A device that is here now."""

    device_id: str  # PCI\VEN_10EC&DEV_8168&SUBSYS_...&REV_15\4&2ce...
    name: str
    compatible: tuple[str, ...] = ()
    driver_version: str = ""

    @property
    def queries(self) -> tuple[str, ...]:
        """What the catalog is searched by: the exact id (with SUBSYS - the driver of this very PC), then the chip's."""
        parts = self.device_id.split("\\")
        exact = re.sub(r"&REV_[0-9A-F]+$", "", "\\".join(parts[:2]), flags=re.IGNORECASE) if len(parts) >= 2 else ""
        chip = CHIP_ID.match(self.device_id)
        found = [exact, chip.group(0) if chip else ""]
        # Audio on Intel's own bus is the same codec on the HD Audio bus in the catalog.
        for item in list(found):
            if item.upper().startswith("INTELAUDIO\\"):
                found.append("HDAUDIO\\" + item.split("\\", 1)[1])
        seen: list[str] = []
        for item in found:
            if item and item not in seen:
                seen.append(item)
        return tuple(seen)


def _present(device_id: str) -> bool:
    """Here now: the configuration manager finds the node only of a device that is plugged in."""
    try:
        node = ctypes.c_uint32()
        return ctypes.windll.cfgmgr32.CM_Locate_DevNodeW(ctypes.byref(node), ctypes.c_wchar_p(device_id), 0) == 0  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def _values(key: Any) -> dict[str, Any]:
    import winreg

    found: dict[str, Any] = {}
    index = 0
    while True:
        try:
            name, value, _kind = winreg.EnumValue(key, index)
        except OSError:
            return found
        found[name] = value
        index += 1


def _subkeys(key: Any) -> list[str]:
    import winreg

    names: list[str] = []
    index = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError:
            return names
        index += 1


def _plain_name(values: dict[str, Any]) -> str:
    """FriendlyName, else DeviceDesc - which reads "@oem12.inf,%desc%;Intel(R) Wi-Fi 6E AX211"."""
    text = str(values.get("FriendlyName") or values.get("DeviceDesc") or "")
    return text.rsplit(";", 1)[-1].strip()


def _driver_version(driver_key: str) -> str:
    import winreg

    if not driver_key:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CLASS_KEY + "\\" + driver_key) as key:
            return str(winreg.QueryValueEx(key, "DriverVersion")[0]).strip()
    except OSError:
        return ""


def present_devices(buses: tuple[str, ...] = BUSES) -> list[Device]:
    """Every device on these buses that is here now."""
    if sys.platform != "win32":
        return []
    import winreg

    devices: list[Device] = []
    for bus in buses:
        try:
            bus_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ENUM_KEY + "\\" + bus)
        except OSError:
            continue
        with bus_key:
            for device in _subkeys(bus_key):
                try:
                    device_key = winreg.OpenKey(bus_key, device)
                except OSError:
                    continue
                with device_key:
                    for instance in _subkeys(device_key):
                        device_id = f"{bus}\\{device}\\{instance}"
                        if not _present(device_id):
                            continue
                        try:
                            with winreg.OpenKey(device_key, instance) as instance_key:
                                values = _values(instance_key)
                        except OSError:
                            continue
                        compatible = values.get("CompatibleIDs") or ()
                        devices.append(Device(
                            device_id=device_id.upper(),
                            name=_plain_name(values),
                            compatible=tuple(str(item) for item in compatible) if isinstance(compatible, (list, tuple)) else (str(compatible),),
                            driver_version=_driver_version(str(values.get("Driver") or "")),
                        ))
    return devices


# The cases the firmware calls a portable computer (SMBIOS, System Enclosure): portable, laptop, notebook,
# sub notebook, convertible, detachable - the list Audion Setup Tools goes by.
NOTEBOOK_CASES = frozenset({8, 9, 10, 14, 31, 32})


def chassis_types() -> list[int]:
    """The kinds of case the firmware names, one for each enclosure it describes; nothing when it does not say."""
    if sys.platform != "win32":
        return []
    try:
        table = ctypes.windll.kernel32.GetSystemFirmwareTable  # type: ignore[attr-defined]
        table.restype = ctypes.c_uint
        table.argtypes = (ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
        provider = 0x52534D42  # 'RSMB'
        size = table(provider, 0, None, 0)
        if not size:
            return []
        buffer = ctypes.create_string_buffer(size)
        if table(provider, 0, buffer, size) != size:
            return []
    except (AttributeError, OSError):
        return []
    raw = buffer.raw
    end = min(len(raw), 8 + int.from_bytes(raw[4:8], "little"))
    found: list[int] = []
    position = 8
    while position + 4 <= end:
        kind, length = raw[position], raw[position + 1]
        if kind == 127 or length < 4:
            break
        if kind == 3 and length > 5:
            found.append(raw[position + 5] & 0x7F)  # the top bit is the lock on the case, not its kind
        # Past the formatted part, then past the strings that end with two zero bytes.
        position += length
        while position + 1 < end and not (raw[position] == 0 and raw[position + 1] == 0):
            position += 1
        position += 2
    return found


def is_notebook() -> bool:
    return any(kind in NOTEBOOK_CASES for kind in chassis_types())


@dataclass
class Snapshot:
    """The devices read once for a run of questions: every kind of driver asks the same list."""

    read: Callable[[], list[Device]] = present_devices
    _devices: list[Device] | None = field(default=None, repr=False)

    def devices(self) -> list[Device]:
        if self._devices is None:
            self._devices = self.read()
        return self._devices


def matches(device: Device, spec: dict[str, str]) -> bool:
    """One rule of a kind of driver: `id` - of the device id, `compatible` - of one of its compatible ids
    (the class of the device, CC_0200 a network card), `name` - of its name. Each is a regular expression."""
    if not re.search(str(spec.get("id") or ""), device.device_id, re.IGNORECASE):
        return False
    compatible = str(spec.get("compatible") or "")
    if compatible and not any(re.search(compatible, item, re.IGNORECASE) for item in device.compatible):
        return False
    name = str(spec.get("name") or "")
    return not name or bool(re.search(name, device.name, re.IGNORECASE))


def find(specs: list[dict[str, str]], devices: list[Device]) -> list[Device]:
    """The devices any of the rules is about."""
    return [device for device in devices if any(matches(device, spec) for spec in specs or [])]
