#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""Build the Fedora package (RPM) of krunner-abbr.

    ./build_rpm.py                      # source and binary RPM in build/rpm/
    ./build_rpm.py --srpm               # only the source RPM (what Copr needs)
    ./build_rpm.py --mock fedora-44-x86_64   # rebuild in a clean mock chroot
    ./build_rpm.py --copr nivaca/krunner-abbr  # send the source RPM to Copr

The version comes from __version__ in krunner_abbr.py. The sources are the files of the
git working tree (also the ones not committed yet, but not the ignored ones), so you can
test a package before committing.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "krunner-abbr"
BUILD = HERE / "build"
SKIP = re.compile(r"(^|/)(__pycache__|build)(/|$)|\.pyc$")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def need(tool: str, package: str) -> None:
    if not shutil.which(tool):
        sys.exit(f"{tool} is missing: sudo dnf install {package}")


def version() -> str:
    m = re.search(r'^__version__ = "([^"]+)"', (HERE / "krunner_abbr.py").read_text(), re.M)
    if not m:
        sys.exit("no __version__ in krunner_abbr.py")
    return m[1]


def source_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=HERE, capture_output=True, text=True, check=True).stdout
    return sorted(f for f in out.splitlines() if not SKIP.search(f) and (HERE / f).is_file())


def git_identity() -> str:
    get = lambda key: subprocess.run(["git", "config", key], cwd=HERE, capture_output=True,
                                     text=True).stdout.strip()
    return f"{get('user.name') or 'Nicolas Vaughan'} <{get('user.email') or 'nobody@example.org'}>"


def make_tarball(ver: str, dest: Path) -> Path:
    tarball = dest / f"{NAME}-{ver}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for f in source_files():
            tar.add(HERE / f, arcname=f"{NAME}-{ver}/{f}")
    return tarball


def make_spec(ver: str, release: str, dest: Path) -> Path:
    date = datetime.now().strftime("%a %b %d %Y")
    changelog = f"* {date} {git_identity()} - {ver}-{release}\n- Build from the git working tree"
    spec = (HERE / "packaging" / f"{NAME}.spec.in").read_text()
    spec = spec.replace("@VERSION@", ver).replace("@RELEASE@", release)
    spec = spec.replace("@CHANGELOG@", changelog)
    path = dest / f"{NAME}.spec"
    path.write_text(spec)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", default="1", help="RPM release number (default 1)")
    ap.add_argument("--srpm", action="store_true", help="build only the source RPM")
    ap.add_argument("--mock", metavar="CHROOT",
                    help="rebuild the source RPM with mock, e.g. fedora-44-x86_64")
    ap.add_argument("--copr", metavar="PROJECT",
                    help="submit the source RPM to Copr, e.g. nivaca/krunner-abbr")
    args = ap.parse_args()

    need("rpmbuild", "rpm-build")
    if args.mock:
        need("mock", "mock")
    if args.copr:
        need("copr-cli", "copr-cli")

    ver = version()
    top = BUILD / "rpmbuild"
    shutil.rmtree(top, ignore_errors=True)
    for d in ("SOURCES", "SPECS", "BUILD", "RPMS", "SRPMS"):
        (top / d).mkdir(parents=True)
    tarball = make_tarball(ver, top / "SOURCES")
    spec = make_spec(ver, args.release, top / "SPECS")
    print(f"{NAME} {ver}-{args.release}: {len(source_files())} files in {tarball.name}")

    only_source = args.srpm or args.mock or args.copr
    run(["rpmbuild", "--define", f"_topdir {top}", "-bs" if only_source else "-ba", str(spec)])

    out = BUILD / "rpm"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for rpm in top.glob("*RPMS/**/*.rpm"):
        shutil.copy2(rpm, out)
    srpm = next(out.glob("*.src.rpm"))

    if args.mock:
        run(["mock", "-r", args.mock, "--rebuild", str(srpm), "--resultdir", str(out)])
    if args.copr:
        run(["copr-cli", "build", args.copr, str(srpm)])

    print("\nPackages in", out)
    for rpm in sorted(out.glob("*.rpm")):
        print("  ", rpm.name)
    binary = [r for r in out.glob("*.noarch.rpm")]
    if binary:
        print(f"\nInstall it with:  sudo dnf install {binary[0]}")


if __name__ == "__main__":
    main()
