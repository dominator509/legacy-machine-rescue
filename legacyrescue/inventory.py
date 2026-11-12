"""Inventory a legacy PC disk: boot chain, config, CNC/hardware risk hints."""
import re
from . import image as img

BOOT_FILES = ["IO.SYS", "MSDOS.SYS", "COMMAND.COM", "KERNEL.SYS", "CONFIG.SYS", "AUTOEXEC.BAT",
              "WIN.COM", "WINDOWS/SYSTEM.INI", "WINDOWS/WIN.INI", "WINDOWS/SYSTEM/VMM32.VXD",
              "IBMBIO.COM", "IBMDOS.COM", "CONFIG.SYS"]
# Hints are keyword matches: they flag things to check on the real machine, nothing more.
HW_PATTERNS = [
    ("Hardware key / dongle driver", re.compile(r"hasp|sentinel|rockey|dongle|hardlock|sensilock|wibu|codemeter|\bsafenet\b", re.I)),
    ("Serial/DNC transfer (COM port)", re.compile(r"\bdnc\b|\bcom[1-4]\b|serial|kermit|procomm|telix|xmodem|zmodem|mode\s+com", re.I)),
    ("Motion/IO control card driver", re.compile(r"motion|servo|encoder|galil|delta ?tau|pmac|acorn|mesa|smartcnc|axis|dsp|\bpcl\d|dmc", re.I)),
    ("Network / file share", re.compile(r"\bnet(start|bind)?\b|ndis|ethernet|netbeui|lanman|packet\s*driver|pktdrv", re.I)),
    ("Real-mode memory manager", re.compile(r"himem|emm386|qemm|umb|loadhigh|lh\s", re.I)),
    ("CD-ROM / ASPI driver", re.compile(r"mscdex|cdrom|aspi|oakcdrom", re.I)),
]
CNC_NAMES = re.compile(r"mach[23]|fanuc|haas|mazak|heidenhain|okuma|mastercam|surfcam|edgecam|gibbs|cncpro|fadal|bridgeport|dnc|gcode|\.nc$|\.tap$|\.ngc$|\.mpf$", re.I)


def _find(files, name):
    n = name.lower()
    for p, e in files:
        if p.lower().lstrip("/") == n:
            return p, e
    return None


def inspect(path):
    data, info = img.load(path)
    rep = {"image": info, "partitions": img.partitions(data), "volumes": [], "warnings": [],
           "hardware_flags": [], "cnc_files": []}
    vols = img.volumes(data)
    if not vols:
        rep["warnings"].append("No readable FAT volume found. NTFS, exFAT, Linux or unknown "
                               "formats are not parsed; the image may still boot in an emulator.")
    mbr_hint = img.boot_code_hint(data, 0)
    rep["boot_sector_hint"] = mbr_hint
    for desc, v in vols:
        files = [(p, e) for p, e in v.walk() if not e["dir"]]
        os_name = "Unknown"
        present = {n: bool(_find(files, n)) for n in BOOT_FILES}
        if present["WIN.COM"] and (present["WINDOWS/SYSTEM/VMM32.VXD"] or present["WINDOWS/SYSTEM.INI"]):
            os_name = "Windows 95/98/ME (on DOS)"
        elif present["IO.SYS"] and present["COMMAND.COM"]:
            os_name = "MS-DOS / Windows 9x boot files"
        elif present["KERNEL.SYS"]:
            os_name = "FreeDOS"
        elif present["IBMBIO.COM"]:
            os_name = "PC-DOS"
        cfg = {}
        for n in ("CONFIG.SYS", "AUTOEXEC.BAT", "FDCONFIG.SYS", "FDAUTO.BAT", "WINDOWS/SYSTEM.INI"):
            f = _find(files, n)
            if f:
                cfg[n] = v.read(f[1]).decode("cp437", "replace")
        flags = {}
        for n, text in cfg.items():
            for label, rx in HW_PATTERNS:
                m = rx.search(text)
                if m:
                    flags.setdefault(label, []).append("%s: %s" % (n, m.group(0).strip()))
        for p, e in files:
            low = p.lower()
            for label, rx in HW_PATTERNS[:3]:
                if rx.search(e["name"]) and low.endswith((".sys", ".exe", ".com", ".drv", ".vxd", ".386", ".dll")):
                    flags.setdefault(label, []).append("file " + p)
            if CNC_NAMES.search(p):
                rep["cnc_files"].append(p)
        for k, vv in flags.items():
            rep["hardware_flags"].append({"category": k, "evidence": sorted(set(vv))[:6]})
        total = sum(e["size"] for _, e in files)
        rep["volumes"].append({
            "where": desc, "fs": v.kind, "label": v.label, "oem": v.oem,
            "size_bytes": v.total * v.bps, "files": len(files), "bytes_used": total,
            "os": os_name, "boot_files_present": [n for n, ok in present.items() if ok],
            "boot_files_missing": [n for n in ("IO.SYS", "COMMAND.COM") if not present[n]] if os_name.startswith("MS-DOS") or "Windows" in os_name else [],
            "config_files": {k: c[:4000] for k, c in cfg.items()},
        })
    if not any(vl["os"] != "Unknown" for vl in rep["volumes"]):
        rep["warnings"].append("No recognised DOS/Windows 9x boot file set was found.")
    rep["cnc_files"] = rep["cnc_files"][:40]
    return rep
