"""Turn an inventory (and an optional emulator result) into a recovery runbook.
Evidence is labelled: MEASURED (read from the image), EMULATOR (observed boot),
NOT PROVEN (needs the real machine)."""
import hashlib


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def build(rep, boot=None):
    L = []
    a = L.append
    info = rep["image"]
    a("# Recovery runbook")
    a("")
    a("Image: `%s`  " % info["path"].split("/")[-1])
    a("Size: %d bytes (%s)  " % (info["size"], info["container"]))
    a("SHA-256: `%s`" % info.get("sha256", "n/a"))
    a("")
    a("## 1. Evidence summary")
    a("")
    a("| Claim | Status |")
    a("| --- | --- |")
    vols = rep["volumes"]
    unreadable = rep.get("unreadable_volumes", [])
    
    if vols:
        v = vols[0]
        a("| Filesystem %s, %d files readable | MEASURED |" % (v["fs"], v["files"]))
        a("| OS guess: %s | MEASURED (file names only) |" % v["os"])
    elif unreadable:
        v = unreadable[0]
        a("| Filesystem %s detected | NOT READABLE |" % v["fs"])
        # No OS guess line for unreadable volumes
    else:
        a("| Filesystem readable | FAILED |")
    if boot is None:
        a("| Image boots in emulator | NOT RUN |")
    elif boot.get("booted"):
        a("| Image boots in %s | EMULATOR: %s |" % (boot["emulator"], boot["evidence"]))
    else:
        a("| Image boots in %s | EMULATOR: did not boot (%s) |" % (boot["emulator"], boot.get("evidence", "no evidence")))
    a("| Works with the real motion card, dongle, serial/DNC link and CNC controller | **NOT PROVEN** |")
    a("")
    a("An emulator boot proves the disk contents start. It does not prove real hardware works.")
    a("")
    a("## 2. Risks found")
    a("")
    for w in rep["warnings"]:
        a("- WARNING: " + w)
    if not rep["hardware_flags"] and not rep["warnings"]:
        a("- No hardware-dependent drivers or files matched the keyword checks. This is not a guarantee.")
    for f in rep["hardware_flags"]:
        a("- **%s** (check on real machine). Evidence: %s" % (f["category"], "; ".join(f["evidence"])))
    for finding in rep.get("cnc_findings", []):
        a("- **%s**: `%s` (%s). Reason: %s. %s" % (
            finding["category"], finding["path"], finding["where"],
            finding["reason"], finding["note"]))
    if rep["cnc_files"]:
        a("- Possible CNC-related files (filename/extension hints only): " + ", ".join(rep["cnc_files"][:10]))
    a("")
    a("## 3. Steps")
    a("")
    steps = [
        "Do NOT write to the original disk. Make a sector-level copy of it first (for example `dd` or a hardware imager), then verify the SHA-256 above matches the copy.",
        "Keep the original disk or machine powered off and labelled. Store one copy off-site.",
        "Photograph the back of the PC: every card, cable and dongle, with the port it uses. Record BIOS settings (CMOS) too, since a dead battery can lose them.",
    ]
    for f in rep["hardware_flags"]:
        c = f["category"]
        if "dongle" in c.lower():
            steps.append("Locate the hardware key and keep a spare plan: contact the software vendor about a replacement or license transfer. Test the key on the replacement PC before moving production.")
        if "Serial" in c:
            steps.append("Note COM port numbers, baud rate, parity and handshake used for DNC. Use a USB-serial adapter only after testing a full program transfer.")
        if "Motion" in c:
            steps.append("Identify the motion/IO card model and IRQ/IO settings. These cannot be emulated; source a spare card or an equivalent board.")
    if boot and boot.get("booted"):
        steps.append("Boot a copy of the image in the emulator again before any real-hardware change, to confirm the image is intact.")
    else:
        steps.append("Run the emulator boot test. If it fails, the image may be damaged, need a different BIOS/CPU setting, or rely on hardware.")
    steps.append("Restore the verified copy to a spare drive (an IDE-to-CompactFlash adapter is common) and test it on the real machine with the machine's axes disabled or in a safe state.")
    steps.append("Only after a supervised dry run, return the machine to production.")
    for i, s in enumerate(steps, 1):
        a("%d. %s" % (i, s))
    a("")
    a("## 4. Disk inventory")
    a("")
    for v in vols:
        a("- %s: %s, label `%s`, %d files, OS: %s" % (v["where"], v["fs"], v["label"], v["files"], v["os"]))
        a("  - Boot files present: " + (", ".join(v["boot_files_present"]) or "none"))
        for n in v["config_files"]:
            a("  - Has config file: " + n)
    
    if unreadable:
        a("")
        a("## 5. Unreadable volumes detected")
        a("")
        for v in unreadable:
            a("- %s: %s (detected, not readable)" % (v["where"], v["fs"]))
    return "\n".join(L) + "\n"
