"""The full editor embedded in the project shell, with one managed save target."""
from pathlib import Path
from copy import deepcopy
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QToolBar, QFileDialog, QInputDialog, QApplication, QWidget, QPushButton, QMenu, QMessageBox
from cutraft.app import MainWindow
from cutraft.model import Sequence, uid


class EditorSurface(MainWindow):
    stateChanged=Signal()

    def __init__(self, shell):
        self.shell=shell
        self._internal_open=False
        super().__init__()
        # A Python application-wide filter wraps native objects during destruction.
        # Qt/Shiboken can crash before eventFilter is entered; use widget key events.
        QApplication.instance().removeEventFilter(self)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setMinimumSize(850,570)
        self.menuBar().setNativeMenuBar(False)
        self.save_action.setShortcut(QKeySequence())
        for action in self.findChildren(QAction):
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.addAction(action)
        self.library_tabs.setTabVisible(1,False)
        self.library_tabs.setTabText(1,'編集版・構成')
        self.library_tabs.tabBar().hide()
        self.sequence_label.hide()
        self.statusBar().showMessage('素材を追加して、そのままタイムラインで編集できます。')
        # Escape from editing always returns to the same app, not a separate window.
        self.assets.setAlternatingRowColors(True)
        self.assets.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.assets.customContextMenuRequested.connect(self.asset_menu)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.structure_menu)
        for button in self.findChildren(QPushButton):
            replacements={'V＋':'映像＋','A＋':'音声＋','テロップ＋':'字幕＋','素材を読み込む…':'素材を追加…'}
            if button.text() in replacements:button.setText(replacements[button.text()])

    def build_actions(self):
        super().build_actions()
        for toolbar in self.findChildren(QToolBar):toolbar.hide()
        original=list(self.menuBar().actions())
        for action in original:self.menuBar().removeAction(action)
        # Original file actions must not retain invisible shortcuts that create a
        # second library or change the save target outside the selected project.
        old_file=original[0].menu()
        for action in old_file.actions():
            if action not in (self.save_action,self.import_action,self.mp4_export_action,self.xml_export_action):
                action.setShortcut(QKeySequence())
        video=self.menuBar().addMenu('動画')
        video.addAction('動画一覧へ',lambda:self.shell.open_desk())
        video.addAction(self.save_action)
        video.addAction('素材を追加…',self.import_assets)
        video.addAction(self.mp4_export_action)
        # Keep all edit/keyframe/display/playback commands, but move composition
        # management out of the normal project-saving flow.
        for action in original[1:]:self.menuBar().addAction(action)
        advanced=self.menuBar().addMenu('詳細')
        advanced.addAction('別の編集版を追加…',self.new_sequence)
        toggle=advanced.addAction('編集版・構成を表示');toggle.setCheckable(True)
        toggle.toggled.connect(self.show_structure)
        advanced.addSeparator()
        advanced.addAction(self.xml_export_action)
        advanced.addAction('FCPXMLから読み込み…',self.open_xml)
        advanced.addAction('従来の編集データを取り込む…',self.shell.import_legacy)
        toolbar=QToolBar('編集ツール',self);toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for action in (self.undo_action,self.redo_action,self.split_action,self.delete_action):toolbar.addAction(action)
        toolbar.addSeparator()
        toolbar.addAction('字幕ライブラリ',self.open_subtitles)
        toolbar.addAction('テロップ作成',self.new_title)
        toolbar.addAction('素材を追加',self.import_assets)
        toolbar.addSeparator()
        automation=self.menuBar().addMenu('自動編集')
        for title,method in [('自動文字起こし・仮テロップ',self.open_transcription),
                             ('選択テロップのデザインを一括変更',self.replace_caption_style),
                             ('無音カット',self.open_silence),('音量・BGM',self.open_mixer),('選択した映像と音をまとめる',self.make_compound)]:
            automation.addAction(title,method);toolbar.addAction(title,method)
        toolbar.addSeparator();toolbar.addAction(self.mp4_export_action)
        self.export_button.hide()

    def open_subtitles(self):
        from .subtitle_library import SubtitleLibrary
        SubtitleLibrary(self).exec()

    def open_silence(self):
        if self.shell.desk.busy:self.statusBar().showMessage('収録や素材の処理を完了してから実行してください。');return
        try:
            from .audio_dialogs import SilenceDialog
            SilenceDialog(self).exec()
        except Exception as exc:
            QMessageBox.critical(self,'無音カットを開けません',f'無音カット画面の準備に失敗しました。\n{exc}')

    def open_transcription(self):
        if self.shell.desk.busy:
            self.statusBar().showMessage('収録や素材の処理を完了してから実行してください。');return
        try:
            from .transcription_dialog import TranscriptionDialog
            TranscriptionDialog(self).exec()
        except Exception as exc:
            QMessageBox.critical(self,'自動文字起こしを開けません',f'画面の準備に失敗しました。Studioを再起動しても続く場合は、次の内容をお知らせください。\n{exc}')

    def replace_caption_style(self):
        clips=[clip for clip in self.selected_clips()
               if self.library.assets[clip.asset_id].kind=='title']
        if not clips:
            QMessageBox.information(self,'テロップのデザイン','タイムラインでテロップを選択してください。複数選択したテロップにまとめて適用できます。')
            return
        from cutraft.workspace import title_templates, workspace_root
        templates=title_templates(workspace_root())
        if not templates:
            QMessageBox.information(self,'テロップのデザイン','先に「テロップ作成」からデザインを登録してください。登録した後、同じ操作で選択中のテロップへ一括適用できます。')
            return
        names=[row[0] for row in templates]
        name,ok=QInputDialog.getItem(self,'テロップのデザインを一括変更',
            f'選択中の{len(clips)}個に適用するデザイン',names,0,False)
        if not ok:return
        template=deepcopy(templates[names.index(name)][1])
        sequence=self.sequence
        def apply():
            for clip in clips:
                asset=self.library.assets[clip.asset_id]
                current=clip.title or asset.payload
                spec=deepcopy(template)
                spec['text']=current.get('text',asset.label or 'テロップ')
                spec['canvas']=[sequence.width,sequence.height]
                clip.title=spec
        if self.commit('選択テロップのデザインを一括変更',apply):
            self.preview.sync(True);self.preview.video.update()
            self.statusBar().showMessage(f'{len(clips)}個のテロップに「{name}」を適用しました。',5000)

    def open_mixer(self):
        if self.shell.desk.busy:self.statusBar().showMessage('収録や素材の処理を完了してから実行してください。');return
        from .audio_dialogs import MixerDialog
        MixerDialog(self).exec()

    def show_structure(self, shown):
        self.library_tabs.setTabVisible(1,shown)
        self.library_tabs.tabBar().setVisible(shown)
        self.library_tabs.setCurrentIndex(1 if shown else 0)

    def new_library(self):self.shell.desk.create_project()
    def open_library_folder(self):self.shell.open_project_folder()
    def save_as(self):
        self.statusBar().showMessage('この動画の保存先は統一されています。別の案は「詳細 → 別の編集版を追加」で作れます。',8000)
        return self.save(False)
    def save_folder_as(self):return self.save_as()
    def choose_workspace(self):self.shell.desk.choose_root()

    def open_library(self, path=None):
        if path is None:
            self.shell.open_project_folder();return
        project=self.shell.desk.current
        if project is None or Path(path).resolve()!=self.shell.workspace.library_path(project).resolve():
            self.shell.import_legacy(path);return
        return super().open_library(path)

    def import_assets(self):
        files,_=QFileDialog.getOpenFileNames(self,'この動画に素材を追加','','素材 (*.mp4 *.mov *.mkv *.webm *.wav *.mp3 *.m4a *.aac *.flac *.png *.jpg *.jpeg *.webp);;すべて (*)')
        if files:self.shell.import_files(files)

    def import_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'素材フォルダを追加')
        if folder:self.shell.import_files([folder])

    def add_paths(self,paths):self.shell.import_files(paths)

    def register_paths(self,paths):super().add_paths(paths)

    def new_sequence(self):
        name,ok=QInputDialog.getText(self,'別の編集版','編集版の名前（例：短い版、別案）')
        if not ok or not name.strip():return
        def apply():
            seq=Sequence(id=uid(),name=name.strip(),width=self.sequence.width,height=self.sequence.height,
                         fps_num=self.sequence.fps_num,fps_den=self.sequence.fps_den)
            self.library.sequences.append(seq);self.library.active_id=seq.id
            event=next((e for e in self.library.events if e.id==self.library.active_event_id),None)
            if event:event.sequence_ids.append(seq.id)
        if self.commit('編集版を追加',apply):
            self.preview.reset(self.library);self.refresh();self.show_structure(True)

    def write_mp4(self):
        self.title_inspector.flush()
        if not self.sequence.clips:self.error('先に素材を追加してください。');return
        project=self.shell.desk.selected_project()
        destination=Path(project['folder'])/'04-exports'/(self.sequence.name+'.mp4')
        file,_=QFileDialog.getSaveFileName(self,'完成動画を書き出す',str(destination),'MP4動画 (*.mp4)')
        if file:
            from cutraft.export_dialog import ExportDialog
            self.preview.pause()
            ExportDialog(self.library,self.with_suffix(file,'.mp4'),self).exec()

    def changed(self):super().changed();self.stateChanged.emit()
    def background_saved(self,result):super().background_saved(result);self.stateChanged.emit()
    def save(self,manual=False):
        result=super().save(manual);self.stateChanged.emit();return result

    def eventFilter(self, watched, event):
        if (event.type()==QEvent.Type.KeyPress and self.isVisible() and isinstance(watched,QWidget)
            and (watched is self or self.isAncestorOf(watched)) and not QApplication.activeModalWidget()
            and self.handle_shortcut(event)):
            return True
        return super().eventFilter(watched,event)

    def asset_menu(self,point):
        index=self.assets.indexAt(point);menu=QMenu(self)
        if index.isValid():
            if not any(i.row()==index.row() for i in self.assets.selectedIndexes()):self.assets.selectRow(index.row())
            ids=[self.assets.item(row,0).data(Qt.ItemDataRole.UserRole) for row in sorted({i.row() for i in self.assets.selectedIndexes()})]
            menu.addAction('タイムラインに配置',self.append_asset)
            action=menu.addAction('素材を削除（配置も外す・ゴミ箱へ）',lambda:self.shell.desk.protect(lambda:self.shell.delete_data(asset_ids=ids,label=self.assets.item(index.row(),0).text())))
            action.setEnabled(not self.shell.desk.busy)
        menu.addAction('ゴミ箱を開く',self.shell.show_trash)
        menu.exec(self.assets.viewport().mapToGlobal(point))

    def structure_menu(self,point):
        item=self.tree.itemAt(point)
        if item is None:return
        key=item.data(0,Qt.ItemDataRole.UserRole)
        menu=QMenu(self)
        if key:
            name=item.text(0)
            if str(key).startswith('event:'):
                menu.addAction('素材グループを削除（素材は保持）',lambda:self.shell.desk.protect(lambda:self.shell.delete_data(event_id=key[6:],label=name)))
            else:
                menu.addAction('編集版を削除（ゴミ箱へ）',lambda:self.shell.desk.protect(lambda:self.shell.delete_data(sequence_id=key,label=name)))
        else:
            menu.addAction('この動画を削除（ゴミ箱へ）',lambda:self.shell.desk.protect(lambda:self.shell.delete_project(self.shell.desk.selected_project())))
        menu.addAction('ゴミ箱を開く',self.shell.show_trash)
        menu.exec(self.tree.viewport().mapToGlobal(point))
