"""Export the draw.io diagrams to 600-DPI PNG: draw.io renders, Chrome rasterizes.

    python docs/research/figures/export_diagrams.py            # all of figures/drawio/
    python docs/research/figures/export_diagrams.py "Fig 06"   # names containing "Fig 06"

Two steps, because each tool does one of them well. draw.io itself exports every diagram to SVG
in one batch call (its headless container); exporting PNG directly was unreliable here - it hung
on Fig. 04 and topped out below 600 DPI at page width. Chrome then rasterizes each SVG at a device
scale factor chosen so the image is at least `TARGET_PX` wide - 600 DPI across the 7.16 in of an
IEEE double-column page - and Pillow writes 600 DPI into the PNG, so Word places it at its
intended size. Chrome runs with a throwaway profile so it never touches the user's browser.
`export_manifest.json` records each figure's pixel size and its width at 600 DPI.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
DRAWIO, SVG, PNG = HERE / "drawio", HERE / "svg", HERE / "png"
DPI = 600
TARGET_PX = math.ceil(7.16 * DPI)          # full page width at 600 DPI
MAX_SCALE = 10
CHROME = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
IMAGE = "rlespinasse/drawio-desktop-headless:latest"


def export_svg() -> None:
    """Every .drawio to SVG in one draw.io start-up; the container sees only the figures folder."""
    SVG.mkdir(exist_ok=True)
    subprocess.run(
        ["docker", "run", "--rm", "--shm-size=1g",
         "-e", "DRAWIO_DESKTOP_COMMAND_TIMEOUT=900s",
         "-v", f"{HERE.as_posix()}:/data", IMAGE,
         "-x", "-f", "svg", "-o", "/data/svg", "/data/drawio"],
        check=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )


def svg_size(path: Path) -> tuple[float, float]:
    head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    width = re.search(r'<svg[^>]*\swidth="([\d.]+)', head)
    height = re.search(r'<svg[^>]*\sheight="([\d.]+)', head)
    if width and height:
        return float(width.group(1)), float(height.group(1))
    box = re.search(r'viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+) ([\d.]+)"', head)
    if not box:
        raise ValueError(f"{path.name}: no width/height or viewBox")
    return float(box.group(1)), float(box.group(2))


def rasterize(svg: Path, out: Path) -> dict[str, object]:
    width, height = svg_size(svg)
    scale = min(MAX_SCALE, max(1, math.ceil(TARGET_PX / width)))
    with tempfile.TemporaryDirectory() as profile:
        shot = Path(profile) / "shot.png"
        subprocess.run(
            # draw.io's SVG follows the viewer's colour scheme (`light-dark()`), and Chrome
            # follows Windows: in dark mode the figures came out white-on-black. A print figure
            # is light, so the scheme is forced (Blink: preferredColorScheme 1 = light).
            [str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--blink-settings=preferredColorScheme=1", "--force-color-profile=srgb",
             "--no-first-run", "--no-default-browser-check", f"--user-data-dir={profile}",
             f"--force-device-scale-factor={scale}",
             f"--window-size={math.ceil(width)},{math.ceil(height)}",
             f"--screenshot={shot}", svg.resolve().as_uri()],
            check=True, capture_output=True, timeout=180,
        )
        image = Image.open(shot).convert("RGBA")
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.split()[3])
        flat.save(out, dpi=(DPI, DPI), optimize=True)
    px_w, px_h = flat.size
    return {"svg": svg.name, "png": out.name, "scale": scale, "pixels": [px_w, px_h],
            "inches_at_600_dpi": [round(px_w / DPI, 2), round(px_h / DPI, 2)],
            "dpi_at_page_width": round(px_w / 7.16)}


def main(only: str | None = None) -> None:
    if not CHROME.exists():
        sys.exit(f"Chrome not found at {CHROME}")
    if shutil.which("docker") is None:
        sys.exit("docker is needed for the draw.io export")
    export_svg()
    PNG.mkdir(exist_ok=True)
    manifest = []
    for svg in sorted(SVG.glob("*.svg")):
        if only and only not in svg.stem:
            continue
        record = rasterize(svg, PNG / f"{svg.stem}.png")
        manifest.append(record)
        print(f"  {record['png']}: {record['pixels'][0]}x{record['pixels'][1]} px, "
              f"{record['dpi_at_page_width']} DPI at page width")
    (HERE / "export_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
