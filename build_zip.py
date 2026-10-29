#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""Bundle krunner-abbr in a zip file, to share it or install it with install.sh.

    ./build_zip.py                      # build/krunner-abbr-VERSION.zip
    ./build_zip.py -o ~/krunner-abbr.zip

The files are the same as for the RPM (see build_rpm.py): the git working tree, also
the files not committed yet, but not the ignored ones. They go in a krunner-abbr-VERSION/
folder inside the zip, and keep their permissions, so ./install.sh still runs after unzip.
"""

import argparse
import zipfile
from pathlib import Path

from build_rpm import BUILD, HERE, NAME, source_files, version


def make_zip(ver: str, dest: Path) -> int:
    files = source_files()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            # from_file keeps the Unix permissions (the executable bit of the scripts)
            info = zipfile.ZipInfo.from_file(HERE / f, arcname=f"{NAME}-{ver}/{f}")
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, (HERE / f).read_bytes())
    return len(files)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", type=Path, metavar="FILE",
                    help="where to write the zip (default build/krunner-abbr-VERSION.zip)")
    args = ap.parse_args()

    ver = version()
    dest = (args.output or BUILD / f"{NAME}-{ver}.zip").expanduser().resolve()
    count = make_zip(ver, dest)
    print(f"{NAME} {ver}: {count} files in {dest}")


if __name__ == "__main__":
    main()
