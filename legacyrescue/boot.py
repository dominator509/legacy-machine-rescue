"""Boot a disk image in QEMU (TCG, no network, headless) and record evidence.
Evidence = a real VGA screendump (PPM->PNG) plus the text of the screen, captured
over QMP. The source image is never modified (snapshot mode)."""
import json, os, shutil, socket, subprocess, tempfile, time, zlib, struct


def qemu_cmd():
    base = os.environ.get("LEGACYRESCUE_QEMU_ROOT")
    exe = shutil.which("qemu-system-x86_64")
    env = dict(os.environ)
    args_extra = []
    if base:
        exe = os.path.join(base, "usr/bin/qemu-system-x86_64")
        env["QEMU_MODULE_DIR"] = os.path.join(base, "usr/lib/x86_64-linux-gnu/qemu")
        env["LD_LIBRARY_PATH"] = os.path.join(base, "usr/lib/x86_64-linux-gnu") + ":" + env.get("LD_LIBRARY_PATH", "")
        args_extra = ["-L", os.path.join(base, "usr/share/seabios"), "-L", os.path.join(base, "usr/share/qemu")]
    if not exe:
        raise RuntimeError("qemu-system-x86_64 not found (set LEGACYRESCUE_QEMU_ROOT)")
    return exe, args_extra, env


def _png(ppm_path, png_path):
    d = open(ppm_path, "rb").read()
    parts = d.split(b"\n", 3)
    w, h = map(int, parts[1].split())
    pix = parts[3]
    raw = b"".join(b"\x00" + pix[y * w * 3:(y + 1) * w * 3] for y in range(h))
    def ch(t, b):
        c = struct.pack(">I", len(b)) + t + b
        return c + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    open(png_path, "wb").write(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                               + ch(b"IDAT", zlib.compress(raw, 9)) + ch(b"IEND", b""))
    return w, h


class Qmp:
    def __init__(self, path, timeout=20):
        t0 = time.time()
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX)
                self.s.connect(path)
                break
            except OSError:
                if time.time() - t0 > timeout:
                    raise
                time.sleep(0.2)
        self.f = self.s.makefile("rw")
        self.f.readline()
        self.cmd("qmp_capabilities")

    def cmd(self, name, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        while True:
            r = json.loads(self.f.readline())
            if "return" in r or "error" in r:
                return r


def run(image, outdir, seconds=25, drive="auto", memory=32, cpu="pentium"):
    os.makedirs(outdir, exist_ok=True)
    exe, extra, env = qemu_cmd()
    size = os.path.getsize(image)
    if drive == "auto":
        drive = "floppy" if size <= 2949120 else "hd"
    tmp = tempfile.mkdtemp(prefix="lmr-")
    sock = os.path.join(tmp, "qmp.sock")
    serial = os.path.join(outdir, "serial.log")
    args = [exe, *extra, "-machine", "pc", "-cpu", cpu, "-m", str(memory), "-accel", "tcg",
            "-display", "none", "-vga", "std", "-nic", "none", "-serial", "file:" + serial,
            "-qmp", "unix:" + sock + ",server=on,wait=off", "-no-reboot", "-snapshot", "-boot", "a" if drive == "floppy" else "c"]
    if drive == "floppy":
        args += ["-drive", "file=%s,if=floppy,format=raw,readonly=on" % image]
    else:
        args += ["-drive", "file=%s,format=raw,if=ide" % image]
    p = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    shots = []
    try:
        q = Qmp(sock)
        t0 = time.time()
        marks = sorted(set([3, 8, seconds // 2, seconds]))
        for m in marks:
            time.sleep(max(0, t0 + m - time.time()))
            ppm = os.path.join(outdir, "t%02d.ppm" % m)
            r = q.cmd("screendump", filename=ppm)
            if "error" in r:
                raise RuntimeError(str(r))
            time.sleep(0.3)
            tp = os.path.join(outdir, "t%02d.vram" % m)
            r2 = q.cmd("pmemsave", val=0xB8000, size=4000, filename=tp)
            text = None
            if "error" not in r2:
                time.sleep(0.2)
                raw = open(tp, "rb").read()
                text = "\n".join(raw[i:i + 160:2].decode("cp437", "replace").rstrip() for i in range(0, 4000, 160)).rstrip()
                os.remove(tp)
            png = os.path.join(outdir, "t%02d.png" % m)
            w, h = _png(ppm, png)
            os.remove(ppm)
            shots.append({"t": m, "png": png, "w": w, "h": h, "text": text})
        q.cmd("quit")
    finally:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    verdict = judge(shots)
    return {**verdict, "emulator": "QEMU 6.2 (TCG, %s, %d MB, no network)" % (cpu, memory), "drive": drive,
            "screenshots": shots, "serial_log": serial, "cmdline": " ".join(args[:1] + args[len(extra) + 1:])}


BAD = ("no bootable", "boot failed", "non-system disk", "invalid system disk", "missing operating system",
       "operating system not found", "disk boot failure", "press a key to reboot", "error loading", "no boot device")


def judge(shots):
    """Decide from real evidence: text on screen at the end and whether it changed.
    A DOS-family OS reaching a prompt/menu counts as booted. This is emulator evidence only."""
    last = (shots[-1].get("text") or "") if shots else ""
    low = last.lower()
    bad = [b for b in BAD if b in low]
    if bad:
        return {"booted": False, "evidence": "screen shows '%s'" % bad[0]}
    if not last.strip():
        return {"booted": False, "evidence": "screen stayed blank (graphics mode or no output; check screenshots)"}
    if "seabios" in low and len(low.split()) < 40:
        return {"booted": False, "evidence": "stopped at BIOS screen"}
    first_line = next((l.strip() for l in last.splitlines() if l.strip()), "")
    return {"booted": True, "evidence": "OS output on screen after %ds: '%s'" % (shots[-1]["t"], first_line[:60])}
