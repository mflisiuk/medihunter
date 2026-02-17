"""CLI entrypoint wrapper.

The original project uses a single-file module layout where medihunter.py imports
other local modules (medicover_session.py, medihunter_notifiers.py).

When installed as a package via pip, those local imports fail because they're not
packaged. This wrapper makes sure the repo directory is on sys.path, then
dispatches to medihunter.medihunter().
"""

import os
import sys

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

import medihunter as medihunter_module  # noqa: E402


def main():
    return medihunter_module.medihunter()


if __name__ == "__main__":
    raise SystemExit(main())
