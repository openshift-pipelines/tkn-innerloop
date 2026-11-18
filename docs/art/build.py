#!/usr/bin/env python3
"""Writes dist/presentation.public.md with every art/*.svg picture inlined as a PNG.

A public sharei page only allows `img-src 'self' data:`, so relative picture
files 404 for a visitor. Inline SVG does not work either: the markdown parser
only accepts data:image/(gif|png|jpeg|webp), so each picture is rasterised
(needs rsvg-convert; magick shrinks it) at twice its size and embedded as a PNG.
"""
import base64
import re
import shutil
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
ROOT = DOCS.parent
PICTURE = re.compile(r"!\[([^\]]*)\]\((art/[A-Za-z0-9_.-]+\.svg)\)")


def rasterise(svg: Path) -> bytes:
    png = subprocess.run(
        ["rsvg-convert", "--width", "1440", "--format", "png", str(svg)],
        check=True, capture_output=True,
    ).stdout
    if not shutil.which("magick"):
        return png
    # A split slide's picture box is portrait (about 0.84 wide to tall) and rasters are
    # scaled to cover it, which would crop the landscape art. Centre it on a transparent
    # canvas of that shape instead. Flat colour art keeps its look in 128 colours.
    return subprocess.run(
        ["magick", "png:-", "-strip", "-background", "none", "-gravity", "center",
         "-extent", "1560x1857", "-colors", "128", "-define", "png:compression-level=9", "PNG8:-"],
        check=True, capture_output=True, input=png,
    ).stdout


def inline(match: re.Match) -> str:
    alt, rel = match.groups()
    data = base64.b64encode(rasterise(DOCS / rel)).decode("ascii")
    return f"![{alt}](data:image/png;base64,{data})"


def main() -> None:
    if not shutil.which("rsvg-convert"):
        sys.exit("rsvg-convert is required (librsvg)")
    text = (DOCS / "presentation.md").read_text(encoding="utf-8")
    out = ROOT / "dist" / "presentation.public.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(PICTURE.sub(inline, text), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
