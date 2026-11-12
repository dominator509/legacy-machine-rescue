# Tech steering

- Python 3.8+, standard library only for the core (no pip installs needed to run).
- QEMU (qemu-system-x86_64, TCG, `-nic none`, `-snapshot`) is the only external program. The source image is never written.
- Parsing code must never crash on damaged input: return warnings, or raise ValueError with a clear message.
- Every feature gets a unit test and, where input is untrusted, a fuzz/property test. Run `python -m unittest` before every commit.
- Git from the first commit. Small commits. Author "DSW Enterprises". No personal names, emails or keys in the repo.
- The `.kiro` directory stays committed (never add it to .gitignore).
