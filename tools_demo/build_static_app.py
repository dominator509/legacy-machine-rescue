"""Build the browser version in docs/app. It runs the same Python code (inventory + runbook + report)
inside the browser with Pyodide. No server, nothing is uploaded anywhere. No emulator boot there.
Usage: python -m tools_demo.build_static_app"""
import json, os, shutil, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from tests import fixtures

OUT = os.path.join(ROOT, "docs", "app")
MODULES = ["__init__", "fat", "image", "inventory", "runbook", "report"]


def main():
    py = os.path.join(OUT, "py", "legacyrescue")
    shutil.rmtree(os.path.join(OUT, "py"), ignore_errors=True)
    os.makedirs(py)
    for m in MODULES:
        shutil.copy(os.path.join(ROOT, "legacyrescue", m + ".py"), os.path.join(py, m + ".py"))
    json.dump(MODULES, open(os.path.join(OUT, "py", "manifest.json"), "w"))
    # Synthetic sample: clearly not real shop data.
    files = {
        "CONFIG.SYS": b"DEVICE=HIMEM.SYS\r\nDEVICE=HASPDOS.SYS\r\n",
        "AUTOEXEC.BAT": b"@ECHO OFF\r\nMODE COM1:9600,E,7,1\r\nECHO SYNTHETIC SAMPLE, NOT REAL SHOP DATA\r\n",
        "PART1.NC": b"G21 G90\r\nG0 X0 Y0\r\nM30\r\n",
        "README.TXT": b"Synthetic sample disk for the browser demo.\r\n",
    }
    open(os.path.join(OUT, "sample.img"), "wb").write(fixtures.fat12_image(files, total=720))


if __name__ == "__main__":
    main()
