from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    print(
        "[step2] NOTE: validate_step2_emulator_docker.py is deprecated.\n"
        "[step2] Use: python3 agentbeats/tools/validate_step2_remote_adb.py",
        file=sys.stderr,
    )
    target = Path(__file__).with_name("validate_step2_remote_adb.py")
    return subprocess.run([sys.executable, str(target), *sys.argv[1:]], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

