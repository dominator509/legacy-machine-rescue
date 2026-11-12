"""Tiny synthetic disk images for tests. Built in memory, no downloads, no real shop data."""
import struct

SECTOR = 512


def _boot_sector(total, fat_sectors, root_entries, label=b"TESTDISK   "):
    b = bytearray(SECTOR)
    b[0:3] = b"\xeb\x3c\x90"
    b[3:11] = b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHI", b, 11, SECTOR, 1, 1, 2, root_entries, total, 0xF0, fat_sectors, 18, 2, 0)
    b[36] = 0
    b[38] = 0x29
    b[43:54] = label
    b[54:62] = b"FAT12   "
    b[510:512] = b"\x55\xaa"
    return b


def fat12_image(files=None, total=2880):
    """Valid FAT12 floppy (1.44 MB geometry by default) with root-directory files.
    files: {"NAME.EXT": bytes}. One cluster is one sector. Files must be small (a few KB)."""
    files = files if files is not None else {"CONFIG.SYS": b"DEVICE=HASPDOS.SYS\r\n", "README.TXT": b"hello\r\n"}
    fat_sectors, root_entries = 9, 224
    img = bytearray(total * SECTOR)
    img[0:SECTOR] = _boot_sector(total, fat_sectors, root_entries)
    fat = bytearray(fat_sectors * SECTOR)
    fat[0:3] = b"\xf0\xff\xff"
    root_start = 1 + 2 * fat_sectors
    data_start = root_start + (root_entries * 32 + SECTOR - 1) // SECTOR
    next_cluster = 2

    def set_fat(c, v):
        i = c + c // 2
        cur = struct.unpack_from("<H", fat, i)[0]
        cur = (cur & 0x000F) | (v << 4) if c & 1 else (cur & 0xF000) | v
        struct.pack_into("<H", fat, i, cur)

    for idx, (name, content) in enumerate(files.items()):
        base, _, ext = name.partition(".")
        n = max(1, (len(content) + SECTOR - 1) // SECTOR)
        clusters = list(range(next_cluster, next_cluster + n))
        next_cluster += n
        for k, c in enumerate(clusters):
            set_fat(c, clusters[k + 1] if k + 1 < len(clusters) else 0xFFF)
            off = (data_start + c - 2) * SECTOR
            chunk = content[k * SECTOR:(k + 1) * SECTOR]
            img[off:off + len(chunk)] = chunk
        e = bytearray(32)
        e[0:11] = base.upper().ljust(8).encode()[:8] + ext.upper().ljust(3).encode()[:3]
        e[11] = 0x20
        struct.pack_into("<H", e, 26, clusters[0])
        struct.pack_into("<I", e, 28, len(content))
        o = root_start * SECTOR + idx * 32
        img[o:o + 32] = e
    for k in range(2):
        o = (1 + k * fat_sectors) * SECTOR
        img[o:o + len(fat)] = fat
    return bytes(img)


def ntfs_boot_sector():
    b = bytearray(SECTOR)
    b[0:3] = b"\xeb\x52\x90"
    b[3:11] = b"NTFS    "
    b[510:512] = b"\x55\xaa"
    return bytes(b) + bytes(SECTOR * 63)


def exfat_boot_sector():
    b = bytearray(SECTOR)
    b[0:3] = b"\xeb\x76\x90"
    b[3:11] = b"EXFAT   "
    b[510:512] = b"\x55\xaa"
    return bytes(b) + bytes(SECTOR * 63)


def unknown_sector(size=SECTOR * 64):
    return bytes((i * 7 + 3) & 0xFF for i in range(size))


def mbr_disk(ptype, partition_bytes, start_lba=63):
    """MBR disk with one partition of the given type holding partition_bytes."""
    mbr = bytearray(SECTOR)
    e = bytearray(16)
    e[0] = 0x80
    e[4] = ptype
    struct.pack_into("<II", e, 8, start_lba, len(partition_bytes) // SECTOR)
    mbr[446:462] = e
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr) + bytes(SECTOR * (start_lba - 1)) + partition_bytes
