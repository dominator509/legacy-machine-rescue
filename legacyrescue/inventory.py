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
CNC_NAMES = re.compile(r"mach[23]|fanuc|haas|mazak|heidenhain|okuma|mastercam|surfcam|edgecam|gibbs|cncpro|fadal|bridgeport|turbocnc|dnc|gcode|\.nc$|\.tap$|\.ngc$|\.mpf$", re.I)


# These are inventory hints, never execution or compatibility checks. Match the
# basename only: a directory called DNC must not label every file inside it.
CNC_FILE_RULES = [
    ("Serial/DNC transfer", re.compile(r"dnc|serial|kermit|procomm|telix|xmodem|zmodem|cimco", re.I),
     (".exe", ".com", ".bat", ".ini", ".cfg"), "filename suggests a serial/DNC transfer tool or its settings"),
    ("Controller configuration", re.compile(r"fanuc|haas|mazak|heidenhain|okuma|fadal|cnc|control|param|turbocnc|maxnc|xylotex", re.I),
     (".ini", ".cfg", ".cnf", ".par", ".dat"), "filename suggests controller or CNC settings"),
    ("Hardware key / dongle driver", re.compile(r"hasp|sentinel|rockey|dongle|hardlock|sensilock|wibu|codemeter|safenet|hlvdd", re.I),
     (".sys", ".exe", ".com", ".drv", ".vxd", ".386", ".dll"), "filename suggests a hardware-key driver or utility"),
    ("Motion/IO control card driver", re.compile(r"motion|servo|encoder|galil|deltatau|pmac|smartcnc|dmc", re.I),
     (".sys", ".exe", ".com", ".drv", ".vxd", ".386", ".dll"), "filename suggests a motion/IO driver or utility"),
]
PROGRAM_EXTENSIONS = (".nc", ".tap", ".ngc", ".mpf", ".gcode", ".ncf", ".ssb", ".i", ".eia", ".min", ".spf", ".cnc")
HEIDENHAIN_SIGNATURE_BYTES = 8192
FANUC_SIGNATURE_BYTES = 8192
HEIDENHAIN_BEGIN = re.compile(r"(?im)^\s*(?:\d+\s+)?BEGIN\s+PGM\b")
HEIDENHAIN_BODY = re.compile(r"(?im)^\s*(?:\d+\s+)?(?:TOOL\s+CALL|CYCL\s+DEF)\b")
FANUC_PROGRAM_NAME = re.compile(r"(?i)^O\d{4}$")
FANUC_PROGRAM_HEADER = re.compile(r"(?im)^\s*O\d{4}\b")
FANUC_CODE_BLOCK = re.compile(r"(?im)^\s*N\d+\s+[^\r\n]*\b[GM]\d{1,3}\b[^\r\n]*$")
FANUC_PROGRAM_END = re.compile(r"(?im)^\s*(?:N\d+\s+)?M(?:30|99)\b")
HARDWARE_CHECK_NOTE = "Needs real hardware check; filename/extension or Heidenhain/Fanuc signature match only, not proof the software works."


def heidenhain_program_signature(content):
    """Recognize a narrow Heidenhain Klartext header plus machining command."""
    if isinstance(content, bytes):
        text = content.decode("cp437", "replace")
    else:
        text = str(content)
    return bool(HEIDENHAIN_BEGIN.search(text) and HEIDENHAIN_BODY.search(text))


def fanuc_program_signature(content):
    """Recognize a narrow Fanuc O-number header with numbered G/M blocks and an end code."""
    if isinstance(content, bytes):
        text = content.decode("cp437", "replace")
    else:
        text = str(content)
    return bool(FANUC_PROGRAM_HEADER.search(text)
                and len(FANUC_CODE_BLOCK.findall(text)) >= 2
                and FANUC_PROGRAM_END.search(text))


def cnc_file_findings(path, where="", content=None):
    """Return filename/extension hints and narrow Heidenhain/Fanuc signature hints."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    extension = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    findings = []
    for category, pattern, extensions, reason in CNC_FILE_RULES:
        match = pattern.search(name)
        if extension in extensions and match:
            findings.append({"path": path, "where": where, "category": category,
                             "reason": reason + " (matched '%s')" % match.group(0),
                             "matched_by": "filename", "note": HARDWARE_CHECK_NOTE})
    if extension in PROGRAM_EXTENSIONS:
        if extension == ".gcode":
            category = "Possible G-code program"
            reason = "file extension .gcode may contain a G-code program"
        else:
            category = "Possible CNC program"
            reason = "file extension %s may contain a CNC program" % extension
        findings.append({"path": path, "where": where, "category": category,
                         "reason": reason, "matched_by": "extension", "note": HARDWARE_CHECK_NOTE})
    if extension == ".h" and content is not None and heidenhain_program_signature(content):
        findings.append({"path": path, "where": where, "category": "Possible CNC program",
                         "reason": "file content has a Heidenhain Klartext program header and machining command",
                         "matched_by": "content", "note": HARDWARE_CHECK_NOTE})
    if FANUC_PROGRAM_NAME.fullmatch(name) and content is not None and fanuc_program_signature(content):
        findings.append({"path": path, "where": where, "category": "Possible CNC program",
                         "reason": "file content has a Fanuc O-number header and numbered G/M blocks",
                         "matched_by": "content", "note": HARDWARE_CHECK_NOTE})
    return findings


def _find(files, name):
    n = name.lower()
    for p, e in files:
        if p.lower().lstrip("/") == n:
            return p, e
    return None


def inspect(path):
    data, info = img.load(path)
    rep = {"image": info, "partitions": img.partitions(data), "volumes": [], "warnings": [],
           "hardware_flags": [], "cnc_files": [], "cnc_findings": [], "unreadable_volumes": []}
    
    # Get readable FAT volumes
    vols = img.volumes(data)
    
    # Get unreadable volumes (NTFS, exFAT, Unknown)
    unreadable = img.unreadable(data)
    rep["unreadable_volumes"] = unreadable
    
    if not vols and not unreadable:
        rep["warnings"].append("No readable FAT volume found. NTFS, exFAT, Linux or unknown "
                               "formats are not parsed; the image may still boot in an emulator.")
    elif not vols and unreadable:
        # We have unreadable volumes but no FAT volumes
        fs_types = [v["fs"] for v in unreadable]
        warnings = []
        if "NTFS" in fs_types:
            warnings.append("NTFS volume detected (not readable)")
        if "exFAT" in fs_types:
            warnings.append("exFAT volume detected (not readable)")
        if "Unknown" in fs_types:
            warnings.append("Unknown filesystem detected (not readable)")
        if warnings:
            rep["warnings"].extend(warnings)
    
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
            basename = p.replace("\\", "/").rsplit("/", 1)[-1]
            is_heidenhain_candidate = basename.lower().endswith(".h")
            is_fanuc_candidate = bool(FANUC_PROGRAM_NAME.fullmatch(basename))
            if is_heidenhain_candidate:
                content_prefix = v.read(e, max_bytes=HEIDENHAIN_SIGNATURE_BYTES)
            elif is_fanuc_candidate:
                content_prefix = v.read(e, max_bytes=FANUC_SIGNATURE_BYTES)
            else:
                content_prefix = None
            findings = cnc_file_findings(p, desc, content_prefix)
            rep["cnc_findings"].extend(findings)
            if findings or CNC_NAMES.search(basename):
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
    if rep["volumes"] and not any(vl["os"] != "Unknown" for vl in rep["volumes"]):
        rep["warnings"].append("No recognised DOS/Windows 9x boot file set was found.")
    rep["cnc_files"] = rep["cnc_files"][:40]
    return rep
