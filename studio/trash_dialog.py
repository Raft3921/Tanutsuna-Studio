from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidgetItem,QMenu
from .desk import table,text


class TrashDialog(QDialog):
    def __init__(self,shell):
        super().__init__(shell);self.shell=shell
        self.setWindowTitle('ゴミ箱 — たぬつな');self.resize(800,480)
        layout=QVBoxLayout(self);layout.setContentsMargins(24,22,24,22);layout.setSpacing(14)
        layout.addWidget(text('削除したデータ','PageTitle'))
        layout.addWidget(text('動画・収録・素材・編集版を元へ戻せます。編集中に重なる配置は別のトラックへ戻します。','Muted'))
        self.table=table(['名前','種類','削除日時']);layout.addWidget(self.table,1)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.cellDoubleClicked.connect(lambda *_:self.restore())
        row=QHBoxLayout();self.status=QLabel();row.addWidget(self.status,1)
        restore=QPushButton('選択した項目を戻す');restore.setObjectName('Primary');restore.clicked.connect(self.restore);row.addWidget(restore)
        close=QPushButton('閉じる');close.clicked.connect(self.accept);row.addWidget(close);layout.addLayout(row)
        self.refresh()

    def refresh(self):
        self.entries=self.shell.data_trash.entries();self.table.setRowCount(len(self.entries))
        labels={'project':'動画','take':'収録','media':'素材','sequence':'編集版','event':'素材グループ'}
        for row,item in enumerate(self.entries):
            for col,value in enumerate([item['label'],labels.get(item['kind'],item['kind']),item['date'][:16].replace('T',' ')]):self.table.setItem(row,col,QTableWidgetItem(value))
        self.table.setColumnWidth(1,100);self.table.setColumnWidth(2,160)
        if self.entries:self.table.selectRow(0)
        else:self.status.setText('ゴミ箱は空です。')

    def restore(self):
        row=self.table.currentRow()
        if row<0:return
        try:
            self.shell.restore_data(self.entries[row]['ticket']);self.status.setText('元に戻しました。');self.refresh()
        except Exception as exc:self.status.setText(str(exc))

    def context_menu(self,point):
        index=self.table.indexAt(point)
        if not index.isValid():return
        self.table.selectRow(index.row());menu=QMenu(self)
        menu.addAction('元に戻す',self.restore);menu.exec(self.table.viewport().mapToGlobal(point))
