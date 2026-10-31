"""Sky-blue workspace inspired by Tanutsuna's clear, friendly character."""
from PySide6.QtGui import QPalette, QColor, QFont
from PySide6.QtWidgets import QApplication

STYLE = '''
QWidget { color:#173846; font-size:13px; }
QMainWindow, QWidget#Desk { background:#eaf6fa; }
QWidget#Rail { background:#153746; }
QLabel#Brand { color:#f3fcff; font-size:23px; font-weight:700; padding:10px 4px; }
QLabel#RailCaption { color:#a9cbd5; font-size:11px; padding:0 5px 18px; }
QPushButton#Nav { background:transparent; color:#c1dce3; border:0; border-radius:8px; text-align:left; padding:12px 14px; font-size:14px; }
QPushButton#Nav:hover { background:#214c5d; color:white; }
QPushButton#Nav:checked { background:#286277; color:#d9f8ff; font-weight:600; }
QWidget#Header { background:#fbfeff; border-bottom:1px solid #cfe7ed; }
QLabel#ProjectTitle { font-size:19px; font-weight:600; color:#153746; }
QLabel#PageTitle { font-size:27px; font-weight:650; color:#153746; }
QLabel#SectionTitle { font-size:17px; font-weight:600; }
QLabel#Muted { color:#607f8b; }
QLabel#Eyebrow { color:#167f98; font-size:11px; font-weight:650; }
QLabel#SaveState { color:#187c8e; background:#ddf4f7; border-radius:12px; padding:6px 12px; }
QLabel#Feedback { color:#426875; padding:9px 20px; background:#e0f1f5; }
QFrame#Card, QWidget#Card { background:#ffffff; border:1px solid #cfe5eb; border-radius:12px; }
QPushButton { background:#ffffff; border:1px solid #c6dfe6; border-radius:7px; padding:8px 13px; min-height:18px; }
QPushButton:hover { background:#e7f7fa; border-color:#69bfd0; }
QPushButton:pressed { background:#d5f0f5; }
QPushButton:disabled { color:#94aab1; background:#eef5f7; border-color:#d8e7eb; }
QPushButton#Primary { color:white; background:#178da7; border-color:#178da7; font-weight:600; padding:11px 20px; }
QPushButton#Primary:hover { background:#11788f; }
QPushButton#Primary:disabled { background:#a6cbd3; border-color:#a6cbd3; }
QPushButton#UpdateAction { color:white; background:#148ba5; border:0; border-radius:16px; font-weight:650; padding:8px 15px; }
QPushButton#UpdateAction:hover { background:#0d7188; }
QPushButton#Ghost { border:0; background:transparent; color:#3d7180; }
QLineEdit, QPlainTextEdit, QTextEdit, QDateTimeEdit, QComboBox, QSpinBox, QDoubleSpinBox { background:white; border:1px solid #c8dfe6; border-radius:6px; padding:6px; selection-background-color:#bdeaf1; selection-color:#123b49; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus { border-color:#21a0b8; }
QTableWidget, QListWidget, QTreeWidget { border:0; background:white; alternate-background-color:#f3fafc; selection-background-color:#d7f1f5; selection-color:#153f4b; gridline-color:#e5f0f3; outline:0; }
QTableWidget::item { padding:9px; border-bottom:1px solid #e5f0f3; }
QHeaderView::section { background:#edf7f9; color:#547782; padding:9px; border:0; text-align:left; }
QGroupBox { background:white; border:1px solid #cfe5eb; border-radius:8px; margin-top:15px; padding:15px 9px 9px; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 4px; color:#2e6473; }
QTabWidget::pane { border:1px solid #cfe5eb; background:white; border-radius:6px; }
QTabBar::tab { background:#e5f2f5; color:#527783; border:0; padding:8px 14px; }
QTabBar::tab:selected { background:white; color:#117d96; }
QToolBar { background:white; border:0; border-bottom:1px solid #d5e8ed; spacing:5px; padding:7px; }
QToolButton { border:0; padding:7px; border-radius:5px; }
QToolButton:hover { background:#e1f3f6; }
QStatusBar { background:#eaf6fa; color:#547783; }
QMenu { background:white; border:1px solid #cfe2e8; padding:5px; }
QMenu::item { padding:8px 24px; }
QMenu::item:selected { background:#dcf2f6; }
QSplitter::handle { background:#d2e7ec; }
QScrollBar:vertical, QScrollBar:horizontal { background:#eaf4f6; width:9px; height:9px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#a9cbd3; border-radius:4px; min-height:24px; min-width:24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; height:0; }
QProgressBar { color:#174958; background:#e3f1f4; border:0; border-radius:5px; text-align:center; }
QProgressBar::chunk { background:#26a6bd; border-radius:5px; }
QCheckBox, QRadioButton { spacing:7px; }
QCheckBox::indicator, QRadioButton::indicator { width:16px; height:16px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background:#178da7; border:1px solid #178da7; }
QToolTip { color:#173846; background:#f6fdff; border:1px solid #badde5; padding:5px; }
'''


def apply_theme(app):
    app.setStyle('Fusion')
    palette=app.palette()
    for role,color in ((QPalette.ColorRole.Window,'#eaf6fa'),(QPalette.ColorRole.WindowText,'#173846'),
        (QPalette.ColorRole.Base,'#ffffff'),(QPalette.ColorRole.Text,'#173846'),
        (QPalette.ColorRole.Button,'#ffffff'),(QPalette.ColorRole.ButtonText,'#173846'),
        (QPalette.ColorRole.Highlight,'#bdeaf1'),(QPalette.ColorRole.HighlightedText,'#123b49')):
        palette.setColor(role,QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
