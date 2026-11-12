"""Minimal read-only FAT12/16/32 reader (pure Python, no dependencies)."""
import struct


class FatError(Exception):
    pass


class FatVolume:
    def __init__(self, data, offset=0):
        self.d = data
        self.o = offset
        b = data[offset:offset + 512]
        if len(b) < 512 or b[510:512] != b"\x55\xaa":
            raise FatError("no boot sector signature at offset %d" % offset)
        (self.bps, self.spc, self.rsv, self.nfats, self.rootent, tot16, self.media,
         self.fatsz16) = struct.unpack_from("<HBHBHHBH", b, 11)
        tot32 = struct.unpack_from("<I", b, 32)[0]
        self.total = tot16 or tot32
        if self.bps not in (512, 1024, 2048, 4096) or self.spc == 0 or self.nfats == 0:
            raise FatError("not a FAT boot sector")
        self.fatsz = self.fatsz16 or struct.unpack_from("<I", b, 36)[0]
        self.rootsecs = (self.rootent * 32 + self.bps - 1) // self.bps
        self.first_data = self.rsv + self.nfats * self.fatsz + self.rootsecs
        clusters = (self.total - self.first_data) // self.spc
        self.clusters = clusters
        self.kind = "FAT12" if clusters < 4085 else "FAT16" if clusters < 65525 else "FAT32"
        self.root_cluster = struct.unpack_from("<I", b, 44)[0] if self.kind == "FAT32" else 0
        self.oem = b[3:11].decode("ascii", "replace").strip()
        lab = b[43:54] if self.kind != "FAT32" else b[71:82]
        self.label = lab.decode("ascii", "replace").strip()
        self.fat = self._sec(self.rsv, self.fatsz)

    def _sec(self, n, count=1):
        s = self.o + n * self.bps
        return self.d[s:s + count * self.bps]

    def _next(self, c):
        f = self.fat
        if self.kind == "FAT12":
            i = c + c // 2
            v = struct.unpack_from("<H", f, i)[0]
            v = v >> 4 if c & 1 else v & 0xFFF
            return None if v >= 0xFF8 else v
        if self.kind == "FAT16":
            v = struct.unpack_from("<H", f, c * 2)[0]
            return None if v >= 0xFFF8 else v
        v = struct.unpack_from("<I", f, c * 4)[0] & 0x0FFFFFFF
        return None if v >= 0x0FFFFFF8 else v

    def _chain(self, c, limit=1 << 20):
        out = []
        while c and c >= 2 and len(out) < limit:
            out.append(c)
            c = self._next(c)
        return out

    def _cluster(self, c):
        return self._sec(self.first_data + (c - 2) * self.spc, self.spc)

    def _read_chain(self, c):
        return b"".join(self._cluster(x) for x in self._chain(c))

    def _dir_bytes(self, cluster):
        if cluster == 0 and self.kind != "FAT32":
            return self._sec(self.rsv + self.nfats * self.fatsz, self.rootsecs)
        return self._read_chain(cluster or self.root_cluster)

    def listdir(self, cluster=0):
        raw = self._dir_bytes(cluster)
        lfn = []
        for i in range(0, len(raw), 32):
            e = raw[i:i + 32]
            if len(e) < 32 or e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn = []
                continue
            attr = e[11]
            if attr == 0x0F:
                part = (e[1:11] + e[14:26] + e[28:32]).decode("utf-16-le", "ignore").split("\x00")[0]
                lfn.insert(0, part)
                continue
            if attr & 0x08:
                lfn = []
                continue
            name = e[0:8].decode("cp437").rstrip()
            ext = e[8:11].decode("cp437").rstrip()
            short = name + ("." + ext if ext else "")
            if short in (".", ".."):
                lfn = []
                continue
            hi = struct.unpack_from("<H", e, 20)[0] if self.kind == "FAT32" else 0
            lo = struct.unpack_from("<H", e, 26)[0]
            yield {"name": "".join(lfn) or short, "short": short, "dir": bool(attr & 0x10),
                   "size": struct.unpack_from("<I", e, 28)[0], "cluster": (hi << 16) | lo}
            lfn = []

    def walk(self, cluster=0, path="", depth=0):
        for e in self.listdir(cluster):
            p = path + "/" + e["name"]
            yield p, e
            if e["dir"] and depth < 12 and e["cluster"] >= 2:
                yield from self.walk(e["cluster"], p, depth + 1)

    def read(self, entry):
        return self._read_chain(entry["cluster"])[:entry["size"]]
