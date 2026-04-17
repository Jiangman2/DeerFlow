from __future__ import annotations

from collections import deque
import sys
from pathlib import Path
from typing import Any, cast


def _is_close_rgb(a: tuple[int, int, int], b: tuple[int, int, int], *, tol: int) -> bool:
    return (
        abs(a[0] - b[0]) <= tol
        and abs(a[1] - b[1]) <= tol
        and abs(a[2] - b[2]) <= tol
    )


def _sample_background_rgb(im) -> tuple[int, int, int]:
    w, h = im.size
    pts = [
        (0, 0),
        (w - 1, 0),
        (0, h - 1),
        (w - 1, h - 1),
        (w // 2, 0),
        (w // 2, h - 1),
        (0, h // 2),
        (w - 1, h // 2),
    ]
    px = cast(Any, im.load())
    rs: list[int] = []
    gs: list[int] = []
    bs: list[int] = []
    for x, y in pts:
        r, g, b, _a = cast(tuple[int, int, int, int], px[x, y])
        rs.append(r)
        gs.append(g)
        bs.append(b)
    return (sum(rs) // len(rs), sum(gs) // len(gs), sum(bs) // len(bs))


def _remove_background_and_crop(src: Path, dst: Path, *, pad: int = 8, tol: int = 18) -> None:
    try:
        from PIL import Image
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Missing Pillow (PIL). Install it, then re-run.") from e

    im = Image.open(src).convert("RGBA")
    w, h = im.size
    px = cast(Any, im.load())
    bg = _sample_background_rgb(im)

    visited = [[False] * w for _ in range(h)]
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        q.append((x, 0))
        q.append((x, h - 1))
    for y in range(h):
        q.append((0, y))
        q.append((w - 1, y))

    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        if visited[y][x]:
            continue
        visited[y][x] = True
        r, g, b, a = cast(tuple[int, int, int, int], px[x, y])
        if a == 0:
            continue
        if not _is_close_rgb((r, g, b), bg, tol=tol):
            continue
        px[x, y] = (r, g, b, 0)
        q.append((x - 1, y))
        q.append((x + 1, y))
        q.append((x, y - 1))
        q.append((x, y + 1))

    bbox = im.getbbox()
    if bbox is None:
        raise RuntimeError("Logo image is empty after background removal.")

    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(w, right + pad)
    bottom = min(h, bottom + pad)

    out = im.crop((left, top, right, bottom))
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, format="PNG", optimize=True)


def _write_alpha_mask(src: Path, dst: Path) -> None:
    try:
        from PIL import Image
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Missing Pillow (PIL). Install it, then re-run.") from e

    im = Image.open(src).convert("RGBA")
    alpha = im.getchannel("A")
    mask = Image.new("RGBA", im.size, (0, 0, 0, 0))
    mask.putalpha(alpha)
    dst.parent.mkdir(parents=True, exist_ok=True)
    mask.save(dst, format="PNG", optimize=True)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "Usage: process_cnooc_logo.py <input_png> <output_png> <output_mask_png>",
            file=sys.stderr,
        )
        return 2

    input_png = Path(argv[1]).expanduser()
    output_png = Path(argv[2]).expanduser()
    output_mask_png = Path(argv[3]).expanduser()

    if not input_png.exists():
        print(f"Input file not found: {input_png}", file=sys.stderr)
        return 2

    _remove_background_and_crop(input_png, output_png)
    _write_alpha_mask(output_png, output_mask_png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
