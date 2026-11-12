"""Disk image detection: raw floppy, raw MBR disk, fixed VHD (footer stripped)."""
import struct
from .fat import FatVolume, FatError

PTYPES = {0x01: "FAT12", 0x04: "FAT16<32M", 0x06: "FAT16", 0x0B: "FAT32", 0x0C: "FAT32 LBA",
          0x0E: "FAT16 LBA", 0x0F: "Extended LBA", 0x05: "Extended", 0x07: "NTFS/HPFS/exFAT",
          0x83: "Linux", 0x82: "Linux swap"}


def load(path):
    data = open(path, "rb").read()
    info = {"path": path, "size": len(data), "container": "raw"}
    if len(data) >= 512 and data[-512:-504] == b"conectix":
        info["container"] = "fixed VHD"
        data = data[:-512]
    elif data[:8] == b"conectix":
        raise ValueError("dynamic VHD is not supported yet; convert to raw first")
    elif data[:4] in (b"KDMV", b"QFI\xfb") or data[:8] == b"<<< Orac":
        raise ValueError("VMDK/QCOW2/VDI images are not supported yet; convert to raw first")
    return data, info


def partitions(data):
    out = []
    if data[510:512] != b"\x55\xaa":
        return out
    for i in range(4):
        e = data[446 + 16 * i:462 + 16 * i]
        t = e[4]
        if t == 0:
            continue
        lba, n = struct.unpack_from("<II", e, 8)
        out.append({"index": i + 1, "type": t, "type_name": PTYPES.get(t, "0x%02x" % t),
                    "active": e[0] == 0x80, "lba": lba, "sectors": n})
    return out


def volumes(data):
    """Return list of (description, FatVolume). Handles bare floppy or MBR."""
    vols = []
    try:
        v = FatVolume(data, 0)
        # A valid-looking MBR can also pass; a real FAT has sane BPB and a FAT type label
        vols.append(("whole image", v))
        return vols
    except FatError:
        pass
    for p in partitions(data):
        if p["type"] in (0x01, 0x04, 0x06, 0x0B, 0x0C, 0x0E):
            try:
                vols.append(("partition %d (%s)" % (p["index"], p["type_name"]),
                             FatVolume(data, p["lba"] * 512)))
            except FatError:
                pass
    return vols


def boot_code_hint(data, offset=0):
    b = data[offset:offset + 512]
    t = b.decode("latin-1")
    for k, v in (("MSWIN4.1", "Windows 95 OSR2/98 boot sector"), ("MSDOS5.0", "MS-DOS 5/6 or Win95 boot sector"),
                 ("IBM  3.3", "PC-DOS 3.3 boot sector"), ("FRDOS", "FreeDOS boot sector"),
                 ("NTFS", "NTFS boot sector")):
        if k in t[:11] or (k == "NTFS" and "NTFS" in t[3:11]):
            return v
    if "Invalid system disk" in t or "Non-System" in t or "Non-system" in t:
        return "DOS-style boot sector"
    return None
