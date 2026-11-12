import argparse, json, sys
from . import inventory, runbook


def main(argv=None):
    ap = argparse.ArgumentParser(prog="legacyrescue", description="Inspect a legacy PC disk image and write a recovery runbook.")
    ap.add_argument("image")
    ap.add_argument("--json", action="store_true", help="print the raw inventory as JSON")
    ap.add_argument("--boot-result", help="JSON file from the emulator boot step")
    ap.add_argument("--boot", action="store_true", help="boot the image in QEMU and record evidence")
    ap.add_argument("--evidence-dir", default="evidence", help="where boot screenshots go")
    ap.add_argument("--seconds", type=int, default=25)
    ap.add_argument("--html", help="write a retro CRT HTML report here")
    ap.add_argument("-o", "--out", help="write runbook markdown here")
    a = ap.parse_args(argv)
    try:
        rep = inventory.inspect(a.image)
    except (OSError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    rep["image"]["sha256"] = runbook.sha256(a.image)
    if a.json:
        print(json.dumps(rep, indent=2))
        return 0
    boot = json.load(open(a.boot_result)) if a.boot_result else None
    if a.boot:
        from . import boot as bootmod
        boot = bootmod.run(a.image, a.evidence_dir, seconds=a.seconds)
        boot["emulator"] = boot["emulator"].split(" (")[0]
        json.dump(boot, open(a.evidence_dir + "/boot.json", "w"), indent=2)
    md = runbook.build(rep, boot)
    if a.html:
        from . import report
        report.build(rep, boot, md, a.html)
    if a.out:
        open(a.out, "w").write(md)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
