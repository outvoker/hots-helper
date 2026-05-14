"""PyInstaller entry point.

Exists because the real UI entrypoint
``src/hots_helper/ui/app.py`` uses package-relative imports like
``from ..config import Config``. PyInstaller treats whatever script you
hand it as ``__main__`` with no parent package, so those relative imports
blow up at runtime with::

    ImportError: attempted relative import with no known parent package

This launcher avoids that: it imports the package by its absolute name
and calls the existing ``main()`` function. PyInstaller's analysis
follows the import graph from here and bundles the whole ``hots_helper``
package as a normal package, which keeps relative imports working.
"""

from __future__ import annotations

import sys


def main() -> int:
    from hots_helper.ui.app import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())
