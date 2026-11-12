"""Build the test corpus and run inspect+boot on each image, comparing to the expected verdict.
Expected verdicts are decided by how each image was made, not by the tool's answer."""
import json, os, shutil, struct, sys, time
from legacyrescue import inventory, boot, runbook

IM = "images"
def mk():
    out = {}
    out["floppy-144"] = (IM + "/144m/x86BOOT.img", True)
    out["floppy-120"] = (IM + "/120m/x86BOOT.img", True)
    out["floppy-720"] = (IM + "/720k/x86BOOT.img", True)
    out["harddisk-lite"] = (IM + "/FD13LITE.img", True)
    out["synthetic-cnc"] = (IM + "/synthetic-cnc-shop.img", True)
    d = open(IM + "/FD13LITE.img", "rb").read()
    # fixed VHD wrapper (raw + 512-byte footer starting with 'conectix')
    f = bytearray(512); f[:8] = b"conectix"; f[0x28:0x30] = struct.pack(">Q", len(d)); f[0x3C:0x40] = b"\0\0\0\2"
    open(IM + "/lite-vhd.vhd", "wb").write(d + bytes(f)); out["vhd-wrapped"] = (IM + "/lite-vhd.vhd", True)
    z = bytearray(d); z[:512] = b"\0" * 512
    open(IM + "/ctl-mbr-wiped.img", "wb").write(z); out["control-mbr-wiped"] = (IM + "/ctl-mbr-wiped.img", False)
    open(IM + "/ctl-blank.img", "wb").write(b"\0" * 1474560); out["control-blank-floppy"] = (IM + "/ctl-blank.img", False)
    t = bytearray(d); t[len(d) // 8:] = b"\0" * (len(d) - len(d) // 8)
    open(IM + "/ctl-wiped-files.img", "wb").write(t); out["control-data-wiped"] = (IM + "/ctl-wiped-files.img", None)
    open(IM + "/ctl-garbage.img", "wb").write(os.urandom(1474560)); out["control-random-bytes"] = (IM + "/ctl-garbage.img", False)
    return out

if __name__ == "__main__":
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    rows = []
    for name, (path, exp) in mk().items():
        t = time.time()
        try:
            rep = inventory.inspect(path); fs = rep["volumes"][0]["fs"] if rep["volumes"] else "none"
            files = rep["volumes"][0]["files"] if rep["volumes"] else 0
        except Exception as e:
            fs, files = "ERROR " + str(e)[:40], 0
        b = boot.run(path, "out/corpus/" + name, seconds=secs)
        ok = None if exp is None else (b["booted"] == exp)
        rows.append({"name": name, "fs": fs, "files": files, "booted": b["booted"], "expected_boot": exp,
                     "match": ok, "evidence": b["evidence"], "sec": round(time.time() - t)})
        print(rows[-1], flush=True)
    json.dump(rows, open("out/corpus/results.json", "w"), indent=1)
    print("matches:", sum(1 for r in rows if r["match"]), "of", sum(1 for r in rows if r["match"] is not None))
