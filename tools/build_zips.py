#!/usr/bin/env python3
"""Build one ZIP per skill for uploading to claude.ai (Settings -> Capabilities -> Skills).

    python3 tools/build_zips.py          # -> dist/<skill>.zip

Each ZIP holds the skill folder with a copy of the shared library inside it
(<skill>/_lib/tadawal.py), so the skill works on its own.
"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
LIB = os.path.join(SKILLS, "_lib", "tadawal.py")


def main():
    out = os.path.join(ROOT, "dist")
    os.makedirs(out, exist_ok=True)
    for name in sorted(os.listdir(SKILLS)):
        d = os.path.join(SKILLS, name)
        if name.startswith("_") or not os.path.isfile(os.path.join(d, "SKILL.md")):
            continue
        z = os.path.join(out, name + ".zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for base, dirs, files in os.walk(d):
                dirs[:] = [x for x in dirs if x != "__pycache__"]
                for f in files:
                    p = os.path.join(base, f)
                    zf.write(p, os.path.join(name, os.path.relpath(p, d)))
            zf.write(LIB, os.path.join(name, "_lib", "tadawal.py"))
        print(z)


if __name__ == "__main__":
    main()
