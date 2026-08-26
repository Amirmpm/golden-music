"""Render the reworked ThemePickerPopup in one dark + one light host theme."""
import sys
sys.path.insert(0, '.')
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
app.setApplicationName('GM-Picker-Preview')

from theme_picker import ThemePickerPopup

for name, out in [('royal_gold', '/tmp/picker_dark.png'),
                  ('porcelain',   '/tmp/picker_light.png')]:
    tp = ThemePickerPopup(name)
    tp.show()
    app.processEvents()
    pm = tp.grab()
    ok = pm.save(out, 'PNG')
    print(name, tp.width(), 'x', tp.height(), '->', out, 'saved=', ok, flush=True)
    tp.close()
print('PREVIEW OK', flush=True)
