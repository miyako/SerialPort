#!/usr/bin/env python3
"""Rasterize the original rectangle/polygon SVG and embed the PNG into a 64x64 SVG."""
import argparse
import base64
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET
import zlib

ROOT = Path(__file__).resolve().parents[1]


def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)


def inside_polygon(x, y, points):
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def render(source):
    svg = ET.fromstring(source)
    if svg.attrib.get("viewBox") != "0 0 64 64":
        raise ValueError("icon renderer requires the 64x64 viewBox")
    shapes = []
    for element in svg:
        kind = element.tag.rsplit("}", 1)[-1]
        color = element.attrib["fill"]
        if len(color) != 7 or not color.startswith("#"):
            raise ValueError("icon fills must be six-digit hex colors")
        rgba = bytes.fromhex(color[1:]) + b"\xff"
        if kind == "rect":
            data = tuple(float(element.attrib[key]) for key in ("x", "y", "width", "height"))
        elif kind == "polygon":
            data = [tuple(float(c) for c in p.split(",")) for p in element.attrib["points"].split()]
        else:
            raise ValueError("unsupported SVG element: " + kind)
        shapes.append((kind, data, rgba))
    rows = bytearray()
    for py in range(1024):
        rows.append(0)
        y = (py + 0.5) / 16
        for px in range(1024):
            x = (px + 0.5) / 16
            color = b"\0\0\0\0"
            for kind, data, rgba in shapes:
                if kind == "rect":
                    left, top, width, height = data
                    hit = left <= x < left + width and top <= y < top + height
                else:
                    hit = inside_polygon(x, y, data)
                if hit:
                    color = rgba
            rows.extend(color)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 1024, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


def generate(root, check=False):
    root = Path(root)
    config = json.loads((root / "template.json").read_text())
    directory = root / "component" / config["repository"] / "Resources"
    source = (root / "assets" / "icon-source.svg").read_bytes()
    png = render(source)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="64" height="64" viewport-fill-opacity="0">\n'
           '  <image x="0" y="0" width="64" height="64" xlink:href="data:image/png;base64,'
           + base64.b64encode(png).decode("ascii") + '"/>\n</svg>\n').encode("ascii")
    for name, content in (("logo.png", png), ("logo.svg", svg)):
        path = directory / name
        if check:
            if not path.is_file() or path.read_bytes() != content:
                raise ValueError("icon is missing or stale: " + str(path))
        else:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        generate(args.root, args.check)
    except (ValueError, OSError, ET.ParseError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
