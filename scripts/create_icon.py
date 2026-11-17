"""Render the repository SVG into a Windows ICO without an extra image dependency."""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    renderer = QSvgRenderer(str(root / "assets" / "app_icon.svg"))
    if not renderer.isValid():
        raise RuntimeError("图标 SVG 无法渲染")
    app = QGuiApplication([])
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        canvas = QImage(size, size, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        renderer.render(painter)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not canvas.save(buffer, "PNG"):
            raise RuntimeError("图标 PNG 编码失败")
        images.append((size, bytes(data)))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + len(images) * 16
    entries = []
    for size, png in images:
        entries.append(struct.pack("<BBBBHHII", size if size < 256 else 0,
                                   size if size < 256 else 0, 0, 0, 1, 32,
                                   len(png), offset))
        offset += len(png)
    (root / "assets" / "app_icon.ico").write_bytes(header + b"".join(entries)
                                                  + b"".join(png for _, png in images))
    del app


if __name__ == "__main__":
    main()
