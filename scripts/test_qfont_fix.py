"""Verify the QFont::setPointSizeF fix by driving the real paint path.

The warning fired when Qt handed the delegate an *unresolved* font
(pointSizeF() == -1). The old code computed -1 + 0.5 = -0.5 for the title
font and Qt logged the warning. The new code resolves + clamps, so the
warning can no longer occur. This test renders the delegate to an image
and captures Qt warnings; it fails if the warning appears.
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt, qInstallMessageHandler  # noqa: E402
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem  # noqa: E402

app = QApplication(sys.argv)

# Collect Qt warnings
warnings = []


def handler(mode, ctx, message):
    if mode == Qt.MsgType.QtWarningMsg:
        warnings.append(message)


qInstallMessageHandler(handler)

import main as m  # noqa: E402

lst = QListWidget()
lst.setItemDelegate(m.TrackRowDelegate({}, lst))
item = QListWidgetItem("Bohemian Rhapsody|Queen")
item.setData(m.TrackRowDelegate.TITLE_ROLE, 7)
item.setData(Qt.ItemDataRole.UserRole + 2, True)   # playing row
lst.addItem(item)
lst.resize(400, 60)
lst.show()
app.processEvents()

# Render twice with an explicit second pass — first paint is where the
# unresolved font used to arrive.
for _ in range(2):
    img = QImage(400, 60, QImage.Format.Format_ARGB32)
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    lst.viewport().render(p)
    p.end()
    app.processEvents()

bad = [w for w in warnings if "setPointSizeF" in w or "Point size" in w]
print("qt warnings captured:", len(warnings))
for w in warnings[:5]:
    print("  ", w)
assert not bad, f"QFont warning still present: {bad}"
print("PASS — no setPointSizeF warning on the delegate paint path")
