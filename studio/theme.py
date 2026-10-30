"""A quiet native workspace: ink navigation, warm canvas, restrained teal actions."""
from PySide6.QtGui import QPalette, QColor, QFont
from PySide6.QtWidgets import QApplication

STYLE = '''
QWidget { color:#20303d; font-size:13px; }
QMainWindow, QWidget#Desk { background:#f3f5f6; }
QWidget#Rail { background:#182831; }
QLabel#Brand { color:#f5faf8; font-size:23px; font-weight:700; padding:10px 4px; }
QLabel#RailCaption { color:#9aaeb6; font-size:11px; padding:0 5px 18px; }
QPushButton#Nav { background:transparent; color:#b9c9ce; border:0; border-radius:8px; text-align:left; padding:12px 14px; font-size:14px; }
QPushButton#Nav:hover { background:#233c46; color:white; }
QPushButton#Nav:checked { background:#2a4b51; color:#a4ebd6; font-weight:600; }
QWidget#Header { background:#ffffff; border-bottom:1px solid #dfe6e8; }
QLabel#ProjectTitle { font-size:19px; font-weight:600; color:#172b35; }
QLabel#PageTitle { font-size:27px; font-weight:650; color:#172b35; }
QLabel#SectionTitle { font-size:17px; font-weight:600; }
QLabel#Muted { color:#6b7d87; }
QLabel#Eyebrow { color:#478176; font-size:11px; font-weight:650; }
QLabel#SaveState { color:#357f6b; background:#eaf5ef; border-radius:12px; padding:6px 12px; }
QLabel#Feedback { color:#526b76; padding:9px 20px; background:#edf2f4; }
QFrame#Card, QWidget#Card { background:white; border:1px solid #e0e7e9; border-radius:12px; }
QPushButton { background:white; border:1px solid #d5dfe3; border-radius:7px; padding:8px 13px; min-height:18px; }
QPushButton:hover { background:#edf5f3; border-color:#9cbdb5; }
QPushButton:pressed { background:#e0eeea; }
QPushButton:disabled { color:#a1adb3; background:#f0f3f4; border-color:#e1e7e9; }
QPushButton#Primary { color:white; background:#167e6b; border-color:#167e6b; font-weight:600; padding:11px 20px; }
QPushButton#Primary:hover { background:#116957; }
QPushButton#Primary:disabled { background:#b4ccc5; border-color:#b4ccc5; }
QPushButton#Ghost { border:0; background:transparent; color:#54717c; }
QLineEdit, QPlainTextEdit, QTextEdit, QDateTimeEdit, QComboBox, QSpinBox, QDoubleSpinBox { background:white; border:1px solid #d6e0e4; border-radius:6px; padding:6px; selection-background-color:#d6eee5; selection-color:#17352d; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color:#419b86; }
QTableWidget, QListWidget, QTreeWidget { border:0; background:white; alternate-background-color:#f7f9fa; selection-background-color:#e3f2ec; selection-color:#193e35; gridline-color:#edf1f3; outline:0; }
QTableWidget::item { padding:9px; border-bottom:1px solid #edf1f3; }
QHeaderView::section { background:#f6f8f9; color:#6b7e88; padding:9px; border:0; text-align:left; }
QGroupBox { background:white; border:1px solid #e0e7e9; border-radius:8px; margin-top:15px; padding:15px 9px 9px; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 4px; color:#3f5d67; }
QTabWidget::pane { border:1px solid #e0e7e9; background:white; border-radius:6px; }
QTabBar::tab { background:#edf1f3; color:#657b85; border:0; padding:8px 14px; }
QTabBar::tab:selected { background:white; color:#177762; }
QToolBar { background:white; border:0; border-bottom:1px solid #e2e8ea; spacing:5px; padding:7px; }
QToolButton { border:0; padding:7px; border-radius:5px; }
QToolButton:hover { background:#e4f0ec; }
QStatusBar { background:#f3f6f7; color:#627984; }
QMenu { background:white; border:1px solid #dce4e7; padding:5px; }
QMenu::item { padding:8px 24px; }
QMenu::item:selected { background:#e2f1ea; }
QSplitter::handle { background:#e3e9ec; }
QScrollBar:vertical { background:#f1f5f6; width:9px; }
QScrollBar::handle:vertical { background:#c1cdd2; border-radius:4px; min-height:24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
'''


def apply_theme(app):
    app.setStyle('Fusion')
    palette=app.palette()
    for role,color in ((QPalette.ColorRole.Window,'#f3f5f6'),(QPalette.ColorRole.WindowText,'#20303d'),
        (QPalette.ColorRole.Base,'#ffffff'),(QPalette.ColorRole.Text,'#20303d'),
        (QPalette.ColorRole.Button,'#ffffff'),(QPalette.ColorRole.ButtonText,'#20303d'),
        (QPalette.ColorRole.Highlight,'#d6eee5'),(QPalette.ColorRole.HighlightedText,'#193e35')):
        palette.setColor(role,QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
