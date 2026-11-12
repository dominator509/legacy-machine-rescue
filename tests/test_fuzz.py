import os, random, tempfile, unittest
from legacyrescue import inventory
IMG = os.path.join(os.path.dirname(__file__), "..", "images", "144m", "x86BOOT.img")

class Fuzz(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(IMG), "image missing")
    def test_inspect_never_crashes_on_damaged_images(self):
        base = open(IMG, "rb").read(); rnd = random.Random(1)
        for i in range(150):
            d = bytearray(base)
            for _ in range(rnd.randint(1, 40)):
                d[rnd.randrange(len(d))] = rnd.randrange(256)
            if i % 5 == 0:
                d = d[:rnd.randrange(len(d))]
            fd, p = tempfile.mkstemp(); os.write(fd, bytes(d)); os.close(fd)
            try:
                inventory.inspect(p)
            except ValueError:
                pass
            finally:
                os.remove(p)
