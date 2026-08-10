#!/usr/bin/env python3
"""
BlueNet Windows build script.
Runs PyInstaller with the bluenet.spec and reports the output path.

Usage:
    python build_windows.py            # normal build
    python build_windows.py --clean    # delete dist/ and build/ first
    python build_windows.py --onefile  # single .exe (larger, slower start)
"""

import os
import sys
import shutil
import argparse
import subprocess

PY = sys.executable
ROOT = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(ROOT, "bluenet.spec")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")


def run(cmd: list):
    print(">>", " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, check=True)
    return result.returncode


def main():
    ap = argparse.ArgumentParser(description="Build BlueNet Windows EXE")
    ap.add_argument("--clean",   action="store_true",
                    help="Delete dist/ and build/ before building")
    ap.add_argument("--onefile", action="store_true",
                    help="Produce a single .exe instead of a folder")
    args = ap.parse_args()

    if args.clean:
        for d in (DIST, BUILD):
            if os.path.exists(d):
                print(f"Removing {d}")
                shutil.rmtree(d)

    # Ensure PyInstaller and Pillow are installed
    run([PY, "-m", "pip", "install", "--quiet",
         "pyinstaller", "Pillow"])

    if args.onefile:
        # One-file mode: override the spec with --onefile flag
        run([
            PY, "-m", "PyInstaller",
            "--onefile",
            "--windowed",
            "--name", "BlueNet",
            "--add-data", f"{os.path.join(ROOT, 'sites')};sites",
            "--hidden-import", "tkinter.ttk",
            "--hidden-import", "tkinter.scrolledtext",
            "--hidden-import", "tkinter.messagebox",
            "--hidden-import", "tkinter.simpledialog",
            "--hidden-import", "PIL.Image",
            "--hidden-import", "PIL.ImageTk",
            "--exclude-module", "jnius",
            "--exclude-module", "android",
            "--exclude-module", "kivy",
            os.path.join(ROOT, "windows", "main.py"),
        ])
        out = os.path.join(DIST, "BlueNet.exe")
    else:
        run([PY, "-m", "PyInstaller", "--clean", SPEC])
        out = os.path.join(DIST, "BlueNet", "BlueNet.exe")

    if os.path.exists(out):
        size_mb = os.path.getsize(out) / (1024 * 1024)
        print(f"\n✓ Build complete!")
        print(f"  Output : {out}")
        print(f"  Size   : {size_mb:.1f} MB")
        if not args.onefile:
            folder = os.path.join(DIST, "BlueNet")
            print(f"  Folder : {folder}")
            print(f"\n  To distribute: zip the entire '{folder}' directory.")
            print(f"  To run now  : start \"\" \"{out}\"")
    else:
        print(f"\n✗ Build failed — {out} not found.")
        sys.exit(1)


if __name__ == "__main__":
    main()
