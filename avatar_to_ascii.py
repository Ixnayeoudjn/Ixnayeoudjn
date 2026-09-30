"""Fetch the repository owner's GitHub avatar and render it as ASCII art.

Replaces only the ``text.ascii > tspan`` lines in dark_mode.svg and
light_mode.svg, keeping canvas size, colors, and all animations intact.
Grid dimensions are read from the target SVG itself (60 rows x 104 cols).

Usage:
    USER_NAME=Ixnayeoudjn python avatar_to_ascii.py [username]
    python avatar_to_ascii.py --avatar-url https://.../pic.png
    python avatar_to_ascii.py --help
"""

import argparse
import io
import os
import re
import subprocess
import sys

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from lxml import etree
from PIL import Image, ImageOps

# Sparse (dark pixels) to dense (bright pixels). Bright maps to dense because
# the art renders as light glyphs on the dark theme; same block is reused for
# the light theme, matching the existing files. Glyph family mirrors the
# current SVGs (alphanumeric blocks, M/W/&/8/%/B/@ core).
CHARSET = " `.,:;~+=-!?lI7tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"

SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


def resolve_username(cli_username=None):
    if cli_username:
        return cli_username
    for var in ("USER_NAME", "GITHUB_REPOSITORY_OWNER"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        match = re.search(r"[:/]([^/:]+)/[^/]+?(\.git)?$", (out.stdout or "").strip())
        if out.returncode == 0 and match:
            return match.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    raise SystemExit(
        "error: no username (pass it, or set USER_NAME / GITHUB_REPOSITORY_OWNER)"
    )


def fetch_avatar(username, avatar_url=None):
    if requests is None:
        raise SystemExit("error: requests is not installed")
    token = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = avatar_url or f"https://github.com/{username}.png"
    if avatar_url is None:
        try:
            api = requests.get(
                f"https://api.github.com/users/{username}", headers=headers, timeout=15
            )
            if api.status_code == 200 and api.json().get("avatar_url"):
                url = api.json()["avatar_url"]
                if "?" not in url:
                    url += "?v=4&s=460"
            elif api.status_code == 404:
                raise SystemExit(f"error: GitHub user '{username}' not found")
        except requests.RequestException:
            pass  # fall back to the direct png URL below
    try:
        resp = requests.get(
            url,
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise SystemExit(f"error: avatar download failed: {exc}")
    if not resp.content:
        raise SystemExit("error: avatar download returned empty body")
    return resp.content, url


def _cover_crop(img, aspect):
    """Center-crop to aspect (width divided by height), keep full bleed."""
    w, h = img.size
    if w / h > aspect:
        nw = max(1, round(h * aspect))
        left = (w - nw) // 2
        return img.crop((left, 0, left + nw, h))
    nh = max(1, round(w / aspect))
    top = (h - nh) // 2
    return img.crop((0, top, w, top + nh))


def _fit_contain(img, disp_w, disp_h):
    """Scale full image into display-pixel box, pad with black, no distortion."""
    w, h = img.size
    scale = min(disp_w / w, disp_h / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    thumb = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (disp_w, disp_h), (0, 0, 0))
    canvas.paste(thumb, ((disp_w - nw) // 2, (disp_h - nh) // 2))
    return canvas


def image_to_lines(data, width, height, charset=CHARSET, invert=False,
                   fit="cover", cell_w=None, row_step=None):
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if cell_w and row_step:
        # Grid cells are tall narrow (textLength per column vs row step),
        # so resample in display-pixel space. Skipping this stretches art.
        disp_w = max(1, round(width * cell_w))
        disp_h = max(1, round(height * row_step))
        if fit == "contain":
            img = _fit_contain(img, disp_w, disp_h)
        else:
            img = _cover_crop(img, disp_w / disp_h)
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    gray = gray.resize((width, height), Image.LANCZOS)
    chars = charset if not invert else charset[::-1]
    last = len(chars) - 1
    pixels = list(gray.tobytes())
    lines = []
    for row in range(height):
        line = "".join(
            chars[round(pixels[row * width + col] / 255 * last)]
            for col in range(width)
        )
        lines.append(line.ljust(width)[:width])
    return lines


def read_grid(path):
    tree = etree.parse(path)
    spans = tree.xpath(
        '//svg:text[contains(@class, "ascii")]/svg:tspan', namespaces=SVG_NS
    )
    if not spans:
        raise SystemExit(f"error: no text.ascii > tspan block in {path}")
    width = max(len(s.text or "") for s in spans)
    try:
        cell_w = float(spans[0].get("textLength")) / width
    except (TypeError, ValueError):
        cell_w = None
    try:
        row_step = abs(float(spans[1].get("y")) - float(spans[0].get("y")))
    except (IndexError, TypeError, ValueError):
        row_step = None
    return tree, spans, width, cell_w, row_step


def write_grid(path, lines):
    tree, spans, width, _, _ = read_grid(path)
    if len(lines) != len(spans):
        raise SystemExit(
            f"error: art has {len(lines)} rows, {path} needs {len(spans)}"
        )
    for span, line in zip(spans, lines):
        span.text = line.ljust(width)[:width]
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return len(spans), width


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", nargs="?", default=None)
    parser.add_argument("--dark", default="dark_mode.svg")
    parser.add_argument("--light", default="light_mode.svg")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--charset", default=CHARSET)
    parser.add_argument("--invert", action="store_true")
    parser.add_argument("--avatar-url", default=None)
    parser.add_argument("--fit", choices=("cover", "contain"), default="cover")
    return parser.parse_args(argv)


def main(argv=None):
    import hashlib

    args = parse_args(argv)
    username = resolve_username(args.username)
    _, template_spans, template_width, cell_w, row_step = read_grid(args.dark)
    width = args.width or template_width
    height = args.height or len(template_spans)
    data, url = fetch_avatar(username, args.avatar_url)
    digest = hashlib.sha256(data).hexdigest()[:12]
    print(f"avatar: {url} ({len(data)} bytes, sha {digest})")
    lines = image_to_lines(
        data, width, height, args.charset, args.invert, args.fit, cell_w, row_step
    )
    for path in (args.dark, args.light):
        rows, cols = write_grid(path, lines)
        print(f"{path}: wrote {rows}x{cols} avatar art for {username}")


if __name__ == "__main__":
    main()
