"""Make the Atlas mark and the shortcut icon from the InkVault logo. Run once; the outputs are committed.

    uv run --with pillow python tools/make_mark.py
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "assets" / "inkvault-logo-horizontal.png"
OUT = ROOT / "src" / "inkvault" / "atlas"
BOX = (200, 320, 508, 665)  # the mark alone, without the wordmark (left, top, right, bottom)


def main():
    mark = Image.open(SRC).convert("RGBA").crop(BOX)
    small = mark.resize((round(mark.width * 128 / mark.height), 128), Image.LANCZOS)
    small.save(OUT / "mark.png", optimize=True)
    side = max(mark.size)
    square = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    square.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
    square.save(OUT / "inkvault.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    print("wrote", OUT / "mark.png", "and", OUT / "inkvault.ico")


if __name__ == "__main__":
    main()
