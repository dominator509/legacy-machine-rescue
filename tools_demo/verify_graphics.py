"""Real offline QEMU test, synthetic boot sectors only, not an OS boot.
Run: python -m tools_demo.verify_graphics [new-output-directory]
Requires local QEMU. The three runs take about30seconds. No downloads or shop images.
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from legacyrescue import boot


SECTORS = {
    "graphics": "fa31c08ed88ed0bc007cfbb81300cd10b800a08ec031ffb9007db001f3aab9007db002f3aafaf4ebfd",
    "uniform": "fa31c08ed88ed0bc007cfbb81300cd10b800a08ec031ffb900fab001f3aafaf4ebfd",
    "no-boot": "",
}


def synthetic_image(kind):
    data = bytearray(1474560)
    code = bytes.fromhex(SECTORS[kind])
    if code:
        data[:len(code)] = code
        data[510:512] = b"\x55\xaa"
    return bytes(data)


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="lmr-graphics-"))
    if len(sys.argv) > 1:
        root.mkdir(parents=True, exist_ok=False)
    results = []
    for kind in SECTORS:
        data = synthetic_image(kind)
        image = root / (kind + ".img")
        image.write_bytes(data)
        result = boot.run(str(image.resolve()), str((root / kind).resolve()), seconds=8)
        result["fixture"] = "Synthetic %s boot-sector test. Not an OS or Win9x boot." % kind
        result["image_sha256"] = hashlib.sha256(data).hexdigest()
        result["source_sha256"] = hashlib.sha256(Path(boot.__file__).read_bytes()).hexdigest()
        assert not result["booted"], result
        assert bool(result.get("graphics_output")) == (kind == "graphics"), result
        results.append(result)
    path = root / "results.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(str(path))


if __name__ == "__main__":
    main()
