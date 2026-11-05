from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUT = Path(__file__).parent
W, H = 1600, 900


def font(size: int):
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


F = {"label": font(24), "small": font(18), "tiny": font(15)}


def centered(draw: ImageDraw.ImageDraw, box, value, style="label", fill="#111111"):
    x1, y1, x2, y2 = box
    bb = draw.textbbox((0, 0), value, font=F[style])
    draw.text(((x1 + x2 - (bb[2] - bb[0])) / 2, (y1 + y2 - (bb[3] - bb[1])) / 2 - 2), value, font=F[style], fill=fill)


def dashed(draw, points, fill="#555555", width=2, dash=8, gap=6):
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        length = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(length / (dash + gap)))
        for i in range(steps + 1):
            a = i * (dash + gap) / length
            b = min(1, a + dash / length)
            if a >= 1:
                break
            draw.line((x1 + (x2 - x1) * a, y1 + (y2 - y1) * a, x1 + (x2 - x1) * b, y1 + (y2 - y1) * b), fill=fill, width=width)


def arrow(draw, p1, p2, fill="#111111", width=3):
    draw.line((*p1, *p2), fill=fill, width=width)
    angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    size = 12
    left = (p2[0] - size * math.cos(angle - 0.45), p2[1] - size * math.sin(angle - 0.45))
    right = (p2[0] - size * math.cos(angle + 0.45), p2[1] - size * math.sin(angle + 0.45))
    draw.polygon([p2, left, right], fill=fill)


def left_arrow(draw, x, y, fill="#111111", size=11):
    draw.polygon([(x, y), (x + size, y - size / 2), (x + size, y + size / 2)], fill=fill)


def node(draw, x, title, n):
    draw.rectangle((x, 330, x + 496, 835), fill="#eeeeee", outline="#bbbbbb", width=2)
    for offset in range(x, x + 496, 16):
        draw.line((offset, 330, min(offset + 8, x + 496), 330), fill="#777777", width=2)
        draw.line((offset, 835, min(offset + 8, x + 496), 835), fill="#777777", width=2)
    draw.text((x + 16, 342), title, font=F["small"], fill="#111111")
    inner = x + 66
    draw.rectangle((inner, 375, inner + 364, 433), fill="#ffe5a4", outline="#111111", width=2)
    centered(draw, (inner, 375, inner + 364, 433), "本地 NGINX")
    draw.rectangle((inner, 433, inner + 364, 495), fill="#9bbbf0", outline="#111111", width=2)
    centered(draw, (inner, 433, inner + 364, 495), "API Server")
    draw.rectangle((inner, 495, inner + 364, 557), fill="#d9e6f2", outline="#111111", width=2)
    centered(draw, (inner, 495, inner + 364, 557), "控制器管理器")
    draw.rectangle((inner, 557, inner + 364, 619), fill="#d9e6f2", outline="#111111", width=2)
    centered(draw, (inner, 557, inner + 364, 619), "调度器")
    draw.rectangle((inner, 672, inner + 364, 736), fill="#b7d8a8", outline="#111111", width=2)
    draw.ellipse((inner + 20, 697, inner + 52, 711), fill="#eef7ff", outline="#111111", width=2)
    draw.arc((inner + 20, 704, inner + 52, 728), 0, 180, fill="#111111", width=2)
    centered(draw, (inner + 54, 672, inner + 364, 736), f"etcd-{n}")
    # Match the official topology: local API/etcd relationship runs down the
    # right side of the component stack and points back into both boxes.
    right = inner + 364
    bracket_x = right + 24
    draw.line((right, 464, bracket_x, 464), fill="#111111", width=2)
    draw.line((bracket_x, 464, bracket_x, 704), fill="#111111", width=2)
    draw.line((bracket_x, 704, right, 704), fill="#111111", width=2)
    left_arrow(draw, right, 464)
    left_arrow(draw, right, 704)


def render(frame=0, white=False):
    background = "#ffffff" if white else "black"
    annotation = "#111111" if white else "#eeeeee"
    image = Image.new("RGB", (W, H), background)
    d = ImageDraw.Draw(image)
    worker_x = [80, 380, 680, 980, 1280]
    for x in worker_x:
        d.rectangle((x, 55, x + 220, 127), fill="#f7e2cc")
        centered(d, (x, 55, x + 220, 127), "工作节点")

    d.rectangle((620, 177, 980, 239), fill="#ffe5a4", outline="#111111", width=2)
    centered(d, (620, 177, 980, 239), "NGINX 负载均衡")
    centered(d, (475, 250, 1125, 280), "每个控制平面节点部署一个本地 NGINX", "small", annotation)
    # The reference diagram leaves the worker-to-load-balancer relationship
    # implicit; keep the same clean composition here.

    d.rectangle((42, 672, 1558, 837), fill="#dbeed3", outline="#9ebc94", width=2)
    centered(d, (42, 790, 1558, 837), "堆叠式 etcd 集群", "small")
    node(d, 42, "控制平面节点 1", 1)
    node(d, 552, "控制平面节点 2", 2)
    node(d, 1062, "控制平面节点 3", 3)

    centered(d, (470, 294, 1130, 325), "每个本地 NGINX 健康检查并转发至三个 API Server", "tiny", annotation)
    # Bidirectional etcd replication stays below the etcd boxes.
    arrow(d, (472, 704), (618, 704))
    arrow(d, (618, 704), (472, 704))
    arrow(d, (982, 704), (1128, 704))
    arrow(d, (1128, 704), (982, 704))

    if frame:
        paths = [((290, 155), (290, 375)), ((800, 155), (800, 375)), ((1310, 155), (1310, 375)), ((290, 433), (800, 433)), ((800, 433), (1310, 433)), ((472, 704), (618, 704))]
        p1, p2 = paths[frame % len(paths)]
        t = (frame % 20) / 19
        x = p1[0] + (p2[0] - p1[0]) * t
        y = p1[1] + (p2[1] - p1[1]) * t
        d.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#55e6d0")
    return image


render().save(OUT / "engineering-topology.png")
frames = [render(i) for i in range(41)]
frames[0].save(OUT / "engineering-topology.gif", save_all=True, append_images=frames[1:], duration=50, loop=0, optimize=False)

render(white=True).save(OUT / "engineering-topology-white.png")
white_frames = [render(i, white=True) for i in range(41)]
white_frames[0].save(OUT / "engineering-topology-white.gif", save_all=True, append_images=white_frames[1:], duration=50, loop=0, optimize=False)
render(white=True).save(OUT / "engineering-topology-white-calibrated.png")
