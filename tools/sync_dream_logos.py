"""Resize logos from D:\\dream into PCHost/web/assets/dreamcision/. Run from repo root."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

SRC = Path(r"D:/dream")
OUT = Path("PCHost/web/assets/dreamcision")


def save_contain(im: Image.Image, box: int, path: Path) -> None:
    im = im.convert("RGBA")
    im.thumbnail((box, box), Image.Resampling.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG", optimize=True)


def save_max_height(im: Image.Image, max_h: int, path: Path) -> None:
    im = im.convert("RGBA")
    w, h = im.size
    OUT.mkdir(parents=True, exist_ok=True)
    if h <= max_h:
        im.save(path, "PNG", optimize=True)
        return
    new_w = int(w * (max_h / h))
    im = im.resize((new_w, max_h), Image.Resampling.LANCZOS)
    im.save(path, "PNG", optimize=True)


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"Missing source folder: {SRC}")
    word = Image.open(SRC / "no-star-logo.png")
    mark = Image.open(SRC / "No-star-1.png")
    word2 = Image.open(SRC / "no-star-logo2.png")

    save_max_height(word, 36, OUT / "logo-wordmark-h36.png")
    save_max_height(word, 48, OUT / "logo-wordmark-h48.png")
    save_max_height(word, 96, OUT / "logo-wordmark-h96.png")
    save_max_height(word2, 48, OUT / "logo-wordmark-alt-h48.png")

    save_contain(mark, 192, OUT / "icon-192.png")
    save_contain(mark, 512, OUT / "icon-512.png")
    save_contain(mark, 48, OUT / "favicon-48.png")

    print("OK ->", OUT.resolve())


if __name__ == "__main__":
    main()
