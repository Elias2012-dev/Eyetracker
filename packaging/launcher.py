"""Entry point for the frozen exe.

PyInstaller runs a *script*, not a package, so this stands in for
``python -m eyetrack`` and keeps relative imports working.
"""

import sys

from eyetrack.__main__ import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))