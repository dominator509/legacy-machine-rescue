"""Headless demo capture. Boots a real image in QEMU, saves a real screendump every 0.5 s,
then builds an mp4: title card, the real boot frames (retro CRT filter), result card.
Nothing is faked: every boot frame is a QEMU screendump. Usage:
  python -m tools_demo.capture IMAGE OUT.mp4 [seconds]"""
import os, subprocess, sys, time, shutil, tempfile
from legacyrescue import boot, inventory, runbook

FONT = next((p for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                         "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf") if os.path.exists(p)), None)


def frames(image, d, seconds, every=0.5):
    exe, extra, env = boot.qemu_cmd()
    sock = os.path.join(d, "q.sock")
    args = [exe, *extra, "-machine", "pc", "-cpu", "pentium", "-m", "32", "-accel", "tcg", "-display", "none",
            "-vga", "std", "-nic", "none", "-qmp", "unix:%s,server=on,wait=off" % sock, "-no-reboot", "-snapshot",
            "-boot", "a" if os.path.getsize(image) <= 2949120 else "c"]
    args += (["-drive", "file=%s,if=floppy,format=raw,readonly=on" % image] if os.path.getsize(image) <= 2949120
             else ["-drive", "file=%s,format=raw,if=ide" % image])
    p = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    n = 0
    shots = []
    try:
        q = boot.Qmp(sock)
        t0 = time.time()
        while time.time() - t0 < seconds:
            ppm = os.path.join(d, "f%04d.ppm" % n)
            q.cmd("screendump", filename=ppm)
            time.sleep(0.15)
            png = ppm[:-4] + ".png"
            boot._png(ppm, png); os.remove(ppm)
            shots.append((time.time() - t0, png)); n += 1
            time.sleep(max(0, t0 + n * every - time.time()))
        # final text for the verdict
        tp = os.path.join(d, "v.bin")
        q.cmd("pmemsave", val=0xB8000, size=4000, filename=tp); time.sleep(0.3)
        raw = open(tp, "rb").read()
        text = "\n".join(raw[i:i + 160:2].decode("cp437", "replace").rstrip() for i in range(0, 4000, 160)).rstrip()
        mp = os.path.join(d, "mode.bin")
        mode_result = q.cmd("pmemsave", val=0x449, size=1, filename=mp)
        mode = None
        if "error" not in mode_result:
            with open(mp, "rb") as mode_file:
                data = mode_file.read(2)
            mode = data[0] if len(data) == 1 else None
        q.cmd("quit")
    finally:
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired: p.kill()
    return shots, text, mode


def card(path, lines, color="33ff66"):
    txt = os.path.join(os.path.dirname(path), os.path.basename(path) + ".txt")
    open(txt, "w").write("\n".join(lines))
    f = "fontfile=%s:" % FONT if FONT else ""
    vf = "drawtext=%stextfile=%s:fontcolor=0x%s:fontsize=30:line_spacing=14:x=60:y=80" % (f, txt, color)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x06140b:s=1280x800:d=1",
                    "-vf", vf, "-frames:v", "1", path], check=True)


def main():
    image, out = sys.argv[1], sys.argv[2]
    secs = int(sys.argv[3]) if len(sys.argv) > 3 else 25
    d = tempfile.mkdtemp(prefix="lmr-cap-")
    try:
        rep = inventory.inspect(image)
        shots, text, mode = frames(image, d, secs)
        b = boot.judge([{"t": secs, "text": text, "bios_video_mode": mode}])
        v = rep["volumes"][0] if rep["volumes"] else {}
        name = os.path.basename(image)
        card(d + "/title.png", ["LEGACY MACHINE RESCUE", "", "Old CNC PC dies. Production stops.", "", "Image: " + name,
                                "Real boot test in QEMU, recorded live", "Every frame is a real screen capture"])
        card(d + "/scan.png", ["STEP 1: READ THE DISK", "", "Filesystem: %s" % v.get("fs"), "Files: %s" % v.get("files"),
                               "OS (from file names): %s" % v.get("os"), "",
                               *["Risk: " + h["category"] for h in rep["hardware_flags"][:5]]])
        end = ["STEP 3: RESULT", "", "Emulator boot: %s" % ("BOOTED" if b["booted"] else "UNVERIFIED" if b.get("boot_status") == "unverified" else "DID NOT BOOT"), b["evidence"][:70], "",
               "NOT PROVEN: real motion card, dongle,", "serial/DNC link, CNC controller.", "", "A recovery runbook was written."]
        card(d + "/end.png", end, "ffb000")
        # build segments
        def seg(png, dur, name):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", png, "-t", str(dur), "-r", "10",
                            "-vf", "scale=1280:800", "-pix_fmt", "yuv420p", d + "/" + name], check=True)
        seg(d + "/title.png", 4, "s1.mp4"); seg(d + "/scan.png", 5, "s2.mp4"); seg(d + "/end.png", 6, "s4.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "2", "-i", d + "/f%04d.png",
                        "-vf", "scale=1280:800:flags=neighbor,eq=saturation=1.2,drawgrid=w=iw:h=3:t=1:c=black@0.25,vignette=PI/5",
                        "-r", "10", "-pix_fmt", "yuv420p", d + "/s3.mp4"], check=True)
        lst = d + "/list.txt"
        open(lst, "w").write("".join("file '%s/%s'\n" % (d, n) for n in ("s1.mp4", "s2.mp4", "s3.mp4", "s4.mp4")))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out], check=True)
        print("wrote", out, "boot:", b)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
