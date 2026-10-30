"""Project-local planning, rich notes and a small vector sketch board."""
import json
from pathlib import Path
from copy import deepcopy
from functools import lru_cache
from PySide6.QtCore import Qt, QTimer, Signal, QPointF, QDateTime
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QTextCharFormat
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QPlainTextEdit, QTabWidget, QFormLayout, QComboBox,
    QSpinBox, QColorDialog, QSplitter, QDateTimeEdit, QCheckBox, QScrollArea)
from .workspace import atomic_text


def load_plan(project):
    path = Path(project['folder']) / '00-plan' / 'plan.json'
    if not path.exists():
        return {'version': 1, 'title': project['title'], 'schedule': '', 'notes': '',
                'strokes': [], 'game': '', 'genre': '', 'summary': '', 'checklist': ''}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('企画メモの形式を読み込めません。元のファイルは保持しています。')
    return data


def summary_for(project):
    root=Path(project['folder'])/'00-plan'
    plan=root/'plan.json';summary=root/'summary.json'
    try:
        stamp=plan.stat().st_mtime_ns
    except OSError:return {'title':project['title'],'schedule':'','hasSummary':False}
    try:summary_stamp=summary.stat().st_mtime_ns
    except OSError:summary_stamp=0
    return dict(_cached_summary(str(root),stamp,summary_stamp))


@lru_cache(maxsize=512)
def _cached_summary(folder,stamp,summary_stamp):
    root=Path(folder)
    try:
        data=json.loads((root/('summary.json' if summary_stamp>=stamp else 'plan.json')).read_text(encoding='utf-8'))
        return {'title':data.get('title',''),'schedule':data.get('schedule',''),
                'hasSummary':data.get('hasSummary',bool(data.get('summary','').strip()))}
    except (OSError,ValueError,AttributeError):
        return {'title':'','schedule':'','hasSummary':False,'error':True}


class SketchBoard(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(340, 450)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.color = '#167e6b'; self.pen_width = 3; self.strokes = []; self.active = None

    def point(self, position):
        return [max(0., min(1., position.x()/self.width_pixels())),
                max(0., min(1., position.y()/self.height()))]

    def width_pixels(self):
        return max(1, self.size().width())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.active = {'color': self.color, 'width': self.pen_width,
                           'points': [self.point(event.position())]}
            self.strokes.append(self.active); self.update()

    def mouseMoveEvent(self, event):
        if self.active is not None:
            point = self.point(event.position())
            if point != self.active['points'][-1]: self.active['points'].append(point)
            self.update()

    def mouseReleaseEvent(self, event):
        if self.active is not None:
            self.active = None; self.changed.emit()

    def undo_stroke(self):
        if self.strokes:
            self.strokes.pop(); self.update(); self.changed.emit()

    def paintEvent(self, event):
        painter = QPainter(self); painter.fillRect(self.rect(), QColor('#fffdf7'))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#e8e6dc'), 1))
        for x in range(18, self.width_pixels(), 24):
            for y in range(18, self.height(), 24): painter.drawPoint(x, y)
        for stroke in self.strokes:
            painter.setPen(QPen(QColor(stroke['color']), stroke['width'],
                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            points = [QPointF(x*self.width_pixels(), y*self.height()) for x,y in stroke['points']]
            if len(points) == 1: painter.drawPoint(points[0])
            for a,b in zip(points, points[1:]): painter.drawLine(a,b)


class PlanningPanel(QWidget):
    def __init__(self, desk):
        super().__init__(desk); self.desk = desk; self.project = None; self.loading = False
        self.dirty = False; self.data = {}
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(650)
        self.timer.timeout.connect(self.autosave)
        outer = QVBoxLayout(self); outer.setContentsMargins(28,24,28,20)
        heading = QLabel('企画・メモ'); heading.setObjectName('PageTitle'); outer.addWidget(heading)
        hint = QLabel('思いつきをメモに。概要が決まったら、その企画名で収録・編集へ進めます。')
        hint.setWordWrap(True); outer.addWidget(hint)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs, 1)
        ideas = QWidget(); layout = QVBoxLayout(ideas)
        form = QFormLayout(); self.title = QLineEdit(); form.addRow('企画名（仮）', self.title)
        self.scheduled = QCheckBox('収録予定を決める')
        self.schedule = QDateTimeEdit(QDateTime.currentDateTime()); self.schedule.setCalendarPopup(True)
        self.schedule.setDisplayFormat('yyyy/MM/dd HH:mm'); self.schedule.setEnabled(False)
        date = QHBoxLayout(); date.addWidget(self.scheduled); date.addWidget(self.schedule); date.addStretch()
        form.addRow('スケジュール', date); layout.addLayout(form)
        tools = QHBoxLayout()
        self.bold = QPushButton('太字'); self.bold.setCheckable(True)
        self.font_size = QSpinBox(); self.font_size.setRange(10,48); self.font_size.setValue(14)
        color = QPushButton('文字色'); color.clicked.connect(self.choose_text_color)
        tools.addWidget(self.bold); tools.addWidget(QLabel('文字サイズ')); tools.addWidget(self.font_size); tools.addWidget(color)
        tools.addStretch(); layout.addLayout(tools)
        split = QSplitter()
        self.notes = QTextEdit(); self.notes.setAcceptRichText(False)
        self.notes.setPlaceholderText('企画の種、話したいこと、思いつき… 自由に書いてください。')
        split.addWidget(self.notes)
        sketch = QWidget(); sl = QVBoxLayout(sketch); sl.setContentsMargins(0,0,0,0)
        row = QHBoxLayout(); row.addWidget(QLabel('ペンボード'))
        pen_color = QPushButton('ペンの色'); pen_color.clicked.connect(self.choose_pen_color); row.addWidget(pen_color)
        pen_width = QSpinBox(); pen_width.setRange(1,16); pen_width.setValue(3); row.addWidget(pen_width)
        undo = QPushButton('一筆戻す'); row.addWidget(undo); sl.addLayout(row)
        self.board = SketchBoard(); sl.addWidget(self.board,1); split.addWidget(sketch)
        split.setSizes([520,450]); layout.addWidget(split,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(ideas)
        self.tabs.addTab(scroll, 'アイデア・自由メモ')
        overview = QWidget(); ol = QVBoxLayout(overview); fields = QFormLayout()
        self.game = QLineEdit(); self.game.setPlaceholderText('例：Minecraft（ゲーム以外なら空欄）')
        self.genre = QLineEdit(); self.genre.setPlaceholderText('例：実況・検証・解説')
        self.summary = QPlainTextEdit(); self.summary.setPlaceholderText('誰に、何を見せる？ 見どころ、導入、展開、締めをまとめます。')
        self.checklist = QPlainTextEdit(); self.checklist.setPlaceholderText('使うワールド、素材、話題、準備するもの…')
        fields.addRow('ゲーム', self.game); fields.addRow('動画のジャンル', self.genre)
        fields.addRow('企画の概要', self.summary); fields.addRow('必要なもの・収録メモ', self.checklist)
        ol.addLayout(fields); self.tabs.addTab(overview, '企画の概要')
        footer = QHBoxLayout(); self.feedback = QLabel('企画メモはこのプロジェクトへ自動保存されます。')
        self.feedback.setWordWrap(True); footer.addWidget(self.feedback,1)
        self.next = QPushButton('次へ：企画の概要'); self.next.setObjectName('Primary')
        self.next.clicked.connect(lambda:self.desk.protect(self.advance)); footer.addWidget(self.next); outer.addLayout(footer)
        self.tabs.currentChanged.connect(lambda index:self.next.setText('この企画で収録へ' if index else '次へ：企画の概要'))
        self.bold.toggled.connect(lambda checked:self.format_text(weight=QFont.Weight.Bold if checked else QFont.Weight.Normal))
        self.font_size.valueChanged.connect(lambda value:self.format_text(size=value))
        pen_width.valueChanged.connect(lambda value:setattr(self.board,'pen_width',value))
        undo.clicked.connect(self.board.undo_stroke); self.board.changed.connect(self.mark_dirty)
        self.scheduled.toggled.connect(self.schedule.setEnabled)
        self.scheduled.toggled.connect(self.mark_dirty); self.schedule.dateTimeChanged.connect(self.mark_dirty)
        for widget in (self.title,self.game,self.genre,self.summary,self.checklist,self.notes): widget.textChanged.connect(self.mark_dirty)

    def format_text(self, weight=None, size=None, color=None):
        fmt = QTextCharFormat()
        if weight is not None: fmt.setFontWeight(weight)
        if size is not None: fmt.setFontPointSize(size)
        if color is not None: fmt.setForeground(color)
        self.notes.mergeCurrentCharFormat(fmt); self.notes.setFocus()

    def choose_text_color(self):
        color = QColorDialog.getColor(parent=self)
        if color.isValid(): self.format_text(color=color)

    def choose_pen_color(self):
        color = QColorDialog.getColor(QColor(self.board.color),self)
        if color.isValid(): self.board.color = color.name()

    def load(self, project):
        if self.project is project: return
        self.flush(); data = load_plan(project)
        self.loading = True
        try:
            self.title.setText(data.get('title',project['title']))
            for name in ('game','genre'): getattr(self,name).setText(data.get(name,''))
            self.summary.setPlainText(data.get('summary','')); self.checklist.setPlainText(data.get('checklist',''))
            self.notes.setHtml(data.get('notes',''))
            self.board.strokes = deepcopy(data.get('strokes',[])); self.board.update()
            date = QDateTime.fromString(data.get('schedule',''), Qt.DateFormat.ISODate)
            self.scheduled.setChecked(date.isValid()); self.schedule.setDateTime(date if date.isValid() else QDateTime.currentDateTime())
            self.project = project; self.data = data; self.dirty = False; self.tabs.setCurrentIndex(0)
            self.feedback.setText('企画メモはこのプロジェクトへ自動保存されます。')
        finally: self.loading = False

    def mark_dirty(self, *_):
        if not self.loading and self.project:
            self.dirty = True; self.feedback.setText('メモを保存中…'); self.timer.start()

    def flush(self):
        self.timer.stop()
        if not self.project or not self.dirty: return
        if not Path(self.project['folder']).is_dir():
            raise ValueError('企画の保存先が見つかりません。保存先を戻してから再度保存してください。')
        data = dict(self.data, version=1, title=self.title.text(),
            schedule=self.schedule.dateTime().toString(Qt.DateFormat.ISODate) if self.scheduled.isChecked() else '',
            notes=self.notes.toHtml(), strokes=self.board.strokes, game=self.game.text(),
            genre=self.genre.text(), summary=self.summary.toPlainText(), checklist=self.checklist.toPlainText())
        atomic_text(Path(self.project['folder'])/'00-plan/plan.json',json.dumps(data,ensure_ascii=False,indent=2))
        atomic_text(Path(self.project['folder'])/'00-plan/summary.json',json.dumps(
            {'title':data['title'],'schedule':data['schedule'],'hasSummary':bool(data['summary'].strip())},ensure_ascii=False))
        self.data = data; self.dirty = False; self.feedback.setText('企画メモを保存しました。')

    def autosave(self):
        try: self.flush()
        except Exception as exc: self.feedback.setText('保存できません：'+str(exc))

    def advance(self):
        self.flush()
        if self.tabs.currentIndex() == 0: self.tabs.setCurrentIndex(1); return
        if not self.summary.toPlainText().strip():
            self.feedback.setText('企画の概要を書いてから収録へ進んでください。'); self.summary.setFocus(); return
        title = self.title.text().strip()
        self.desk.workspace.rename(self.project,title)
        surface = self.desk.editor.loaded_editor
        if surface and surface.path == str(self.desk.workspace.library_path(self.project)):
            surface.commit('企画名を動画へ反映',lambda:setattr(surface.sequence,'name',title))
        else:
            from cutraft.storage import load_library, save_library
            path = self.desk.workspace.library_path(self.project)
            library = load_library(str(path)); library.active.name = title; save_library(library,str(path))
        self.desk.refresh_projects(); self.desk.open_recording()
