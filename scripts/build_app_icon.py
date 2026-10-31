"""Create the Studio app icon from the authored transparent tanuki artwork."""
from pathlib import Path
import struct

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QPainter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/tanuki-front.png'
PNG = ROOT / 'studio/assets/tanutsuna-studio-icon.png'
ICO = ROOT / 'studio/assets/tanutsuna-studio.ico'


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise RuntimeError('PNGアイコンを書き出せません。')
    return bytes(buffer.data())


def build() -> None:
    source = QImage(str(SOURCE)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if source.isNull():
        raise FileNotFoundError(SOURCE)
    image = QImage(512, 512, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#0B5364'))
    painter.drawRoundedRect(0, 0, 512, 512, 104, 104)
    face = source.scaled(492, 492, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    painter.drawImage((512-face.width())//2, (512-face.height())//2, face)
    painter.end()
    PNG.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(PNG), 'PNG'):
        raise RuntimeError('Studioアイコンを保存できません。')

    sizes = (16, 24, 32, 48, 64, 128, 256)
    payloads = []
    for size in sizes:
        scaled = image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        payloads.append(png_bytes(scaled))
    header = struct.pack('<HHH', 0, 1, len(sizes))
    entries = []
    offset = 6 + 16 * len(sizes)
    for size, data in zip(sizes, payloads):
        dimension = 0 if size == 256 else size
        entries.append(struct.pack('<BBBBHHII', dimension, dimension, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    ICO.write_bytes(header + b''.join(entries) + b''.join(payloads))


if __name__ == '__main__':
    build()
