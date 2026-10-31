"""One persistent app shell. Projects are the only top-level unit of work."""
from pathlib import Path
import os
import sys
import sqlite3
import json
import re
from contextlib import closing
from PySide6.QtCore import Qt, QProcess, QUrl, QTimer, QDateTime, QItemSelectionModel
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFrame,QLabel,QPushButton,
    QLineEdit,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QStackedWidget,
    QFileDialog,QInputDialog,QMessageBox,QSplitter,QFormLayout,QMenu,QProgressBar)
from .workspace import FOLDERS, DuplicateProject
from .takes import takes_for


def text(value, kind=None):
    item=QLabel(value);item.setWordWrap(True);item.setTextFormat(Qt.TextFormat.PlainText)
    if kind:item.setObjectName(kind)
    return item


def card():
    widget=QFrame();widget.setObjectName('Card')
    layout=QVBoxLayout(widget);layout.setContentsMargins(24,24,24,24);layout.setSpacing(14)
    return widget,layout


def table(headers):
    result=QTableWidget(0,len(headers));result.setHorizontalHeaderLabels(headers)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    result.verticalHeader().hide();result.setShowGrid(False);result.setAlternatingRowColors(True)
    result.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
    result.verticalHeader().setDefaultSectionSize(58)
    return result


class ProductionDesk(QWidget):
    def __init__(self, editor, workspace):
        super().__init__(editor)
        self.editor,self.workspace=editor,workspace
        editor.desk=self
        self.current=None;self.current_page='home'
        self._recording_panel=self._vtuber=self._planning=self._live=None
        self.pages={};self.nav={};self.visible_projects=[]
        self.setObjectName('Desk');self.setAcceptDrops(True)
        outer=QHBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        rail=QWidget();rail.setObjectName('Rail');rail.setFixedWidth(176)
        nav=QVBoxLayout(rail);nav.setContentsMargins(16,24,16,20);nav.setSpacing(6)
        nav.addWidget(text('たぬつな','Brand'));nav.addWidget(text('STUDIO','RailCaption'))
        for key,title in [('home','はじめる'),('record','録画'),('live','ライブ配信'),('edit','編集')]:
            button=QPushButton(title);button.setObjectName('Nav');button.setCheckable(True)
            button.clicked.connect(lambda checked=False,k=key:self.protect(lambda:self.navigate(k)))
            nav.addWidget(button);self.nav[key]=button
        nav.addStretch()
        for key,title in [('library','作品一覧'),('plan','企画・メモ'),('media','素材')]:
            button=self.button(title,lambda k=key:self.navigate(k),'Nav');button.setCheckable(True);nav.addWidget(button);self.nav[key]=button
        trash=self.button('ゴミ箱',self.editor.show_trash,'Nav');nav.addWidget(trash)
        settings=QPushButton('設定');settings.setObjectName('Nav');settings.setCheckable(True)
        settings.clicked.connect(lambda:self.navigate('settings'));nav.addWidget(settings);self.nav['settings']=settings
        nav.addWidget(self.button('V体の詳細設定',lambda:self.navigate('vtuber'),'Nav'))
        nav.addWidget(text('録画も、配信も。','RailCaption'))
        outer.addWidget(rail)
        main=QWidget();body=QVBoxLayout(main);body.setContentsMargins(0,0,0,0);body.setSpacing(0)
        header=QWidget();header.setObjectName('Header');header.setFixedHeight(80)
        bar=QHBoxLayout(header);bar.setContentsMargins(26,12,26,12)
        titles=QVBoxLayout();titles.setSpacing(3)
        self.context=text('YOUR WORKSPACE','Eyebrow');titles.addWidget(self.context)
        self.project_heading=text('動画をつくる','ProjectTitle');titles.addWidget(self.project_heading)
        bar.addLayout(titles);bar.addStretch()
        self.update_action_button=self.button('更新があります',self.editor.update_action,'UpdateAction')
        self.update_action_button.hide();bar.addWidget(self.update_action_button)
        self.capture_badge=self.button('収録停止中',lambda:self.navigate('live' if self.current_page=='live' or (self._live and self._live.busy) else 'record'),'Ghost');bar.addWidget(self.capture_badge)
        self.rename_button=self.button('名前を変更',self.rename_project,'Ghost');bar.addWidget(self.rename_button)
        self.save_label=text('自動保存','SaveState');bar.addWidget(self.save_label)
        self.save_button=self.button('保存',self.editor.save_project);bar.addWidget(self.save_button)
        body.addWidget(header)
        self.stack=QStackedWidget();body.addWidget(self.stack,1)
        self.status=text('録画・ライブ配信・編集から始められます。','Feedback');body.addWidget(self.status)
        outer.addWidget(main,1)
        self.build_home();self.build_library();self.build_media();self.build_settings()
        self.refresh_projects();self.navigate('home')
        self.update_header()
        self.folder_timer=QTimer(self);self.folder_timer.setInterval(5000)
        self.folder_timer.timeout.connect(self.refresh_existing_folders);self.folder_timer.start()

    def button(self,title,function,kind=None):
        button=QPushButton(title)
        if kind:button.setObjectName(kind)
        button.clicked.connect(lambda checked=False:self.protect(function))
        return button

    def protect(self,function):
        try:return function()
        except Exception as exc:
            self.status.setText(str(exc));return False

    @property
    def busy(self):
        return bool((self._live and self._live.busy) or (self._recording_panel and self._recording_panel.busy) or self.editor.media_jobs.busy
            or (self._vtuber and self._vtuber.recording)
            or self.editor.pending_media is not None or (self.editor.loaded_editor and self.editor.loaded_editor.importer.running))

    @property
    def recording_panel(self):
        if self._recording_panel is None:
            from .recording_panel import RecordingPanel
            self._recording_panel=RecordingPanel(self)
            self.add_page('record',self._recording_panel)
        return self._recording_panel

    @property
    def vtuber(self):
        if self._vtuber is None:
            from .vtuber import VtuberPanel
            self._vtuber=VtuberPanel(self,project_getter=self.selected_project)
            self.add_page('vtuber',self._vtuber)
        return self._vtuber

    def add_page(self,key,page):
        self.pages[key]=page;self.stack.addWidget(page)
        return page

    def build_home(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(32,32,32,28);layout.setSpacing(22)
        layout.addWidget(text('何をしますか？','PageTitle'))
        layout.addWidget(text('企画を作らず、そのまま始められます。','Muted'))
        choices=QHBoxLayout();choices.setSpacing(18)
        for title,description,action in [
            ('録画する','画面とV体を録画。保存先は自動で作成します。',self.quick_record),
            ('ライブ配信する','ゲーム画面・V体・音声をYouTubeへ。',lambda:self.navigate('live')),
            ('編集する','保存した作品を選んで編集します。',lambda:self.navigate('library'))]:
            widget,body=card();widget.setFixedHeight(220);body.addWidget(text(title,'SectionTitle'));body.addWidget(text(description,'Muted'));body.addStretch()
            button=self.button(title,action,'Primary');button.setMinimumHeight(46);body.addWidget(button);choices.addWidget(widget)
        layout.addLayout(choices)
        row=QHBoxLayout();row.addWidget(self.button('作品一覧',lambda:self.navigate('library')));row.addWidget(self.button('企画・メモを作る',self.create_project));row.addStretch();layout.addLayout(row)
        layout.addStretch();self.add_page('home',page)

    def quick_record(self):
        if self.busy:raise ValueError('進行中の録画・配信・保存を終了してから始めてください。')
        from datetime import datetime
        project=self.workspace.create('録画 '+datetime.now().strftime('%Y-%m-%d %H-%M-%S'),allow_duplicate=True)
        if self.select(project):return self.navigate('record')
        return False

    def build_library(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(28,27,28,24);layout.setSpacing(20)
        top=QHBoxLayout();heading=QVBoxLayout();heading.addWidget(text('作品一覧','PageTitle'))
        heading.addWidget(text('保存した録画・編集・企画メモを開きます。','Muted'));top.addLayout(heading);top.addStretch()
        top.addWidget(self.button('動画フォルダを開く',self.editor.open_project_folder))
        top.addWidget(self.button('新しい企画を作成',self.create_project,'Primary'));layout.addLayout(top)
        split=QSplitter();left,ll=card();ll.setContentsMargins(16,18,16,16)
        self.search=QLineEdit();self.search.setPlaceholderText('企画・動画名で検索');self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_projects);ll.addWidget(self.search)
        self.projects=table(['企画・動画','収録予定','企画の段階','更新']);self.projects.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.projects.setColumnWidth(1,155);self.projects.setColumnWidth(2,100);self.projects.setColumnWidth(3,95)
        self.projects.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.projects.customContextMenuRequested.connect(self.project_menu)
        self.projects.currentCellChanged.connect(self.select_project)
        self.projects.itemSelectionChanged.connect(self.update_delete_button)
        self.projects.cellDoubleClicked.connect(lambda *_:self.protect(self.continue_project))
        ll.addWidget(self.projects)
        row=QHBoxLayout();row.addWidget(self.button('一覧を更新',self.refresh_projects,'Ghost'));row.addStretch()
        self.delete_button=self.button('選択した作品をゴミ箱へ',lambda:self.editor.delete_projects(self.selected_projects()),'Ghost');row.addWidget(self.delete_button);ll.addLayout(row)
        self.empty=text('まだ企画がありません。\n「新しい企画を作成」から、アイデアを書き始めましょう。','Muted');ll.addWidget(self.empty)
        split.addWidget(left)
        right,rl=card();right.setMinimumWidth(280)
        rl.addWidget(text('選択した作品','Eyebrow'))
        self.hero_title=text('ここから、次の一本を。','PageTitle');rl.addWidget(self.hero_title)
        self.summary=text('動画を選ぶと、素材も編集内容も一緒に開けます。','Muted');rl.addWidget(self.summary)
        self.continue_button=self.button('企画を続ける',self.continue_project,'Primary')
        self.plan_button=self.button('企画・メモを開く',lambda:self.navigate('plan'));rl.addWidget(self.plan_button);rl.addWidget(self.continue_button)
        self.record_button=self.button('この作品に録画する',self.open_recording);rl.addWidget(self.record_button)
        self.media_button=self.button('収録・追加素材を見る',lambda:self.navigate('media'));rl.addWidget(self.media_button)
        rl.addStretch()
        rl.addWidget(text('保存先を選び直す必要はありません。\n編集内容と収録素材は、この動画にまとまります。','Muted'))
        rl.addWidget(self.button('以前の編集データを取り込む',self.editor.import_legacy,'Ghost'))
        split.addWidget(right);split.setSizes([600,340]);layout.addWidget(split,1)
        self.add_page('library',page)

    def build_media(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(28,26,28,24);layout.setSpacing(16)
        top=QHBoxLayout();top.addWidget(text('この動画の素材','PageTitle'));top.addStretch()
        top.addWidget(self.button('素材を追加',self.editor.choose_media,'Primary'));layout.addLayout(top)
        layout.addWidget(text('収録は画面・PC音・声・V体をひとまとまりで扱います。配置済みの収録は重複しません。','Muted'))
        box,bl=card();bl.setContentsMargins(16,16,16,16)
        self.media=table(['素材','種類','状態']);self.media.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.media.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.media.customContextMenuRequested.connect(self.media_menu)
        self.media.cellDoubleClicked.connect(lambda *_:self.place_media())
        bl.addWidget(self.media);layout.addWidget(box,1)
        row=QHBoxLayout();row.addWidget(self.button('選択した素材を編集に置く',self.place_media,'Primary'))
        row.addWidget(self.button('選択した素材を削除',self.delete_selected_media))
        row.addWidget(self.button('収録をすべて編集に置く',self.sync_takes))
        row.addStretch();row.addWidget(self.button('外部参照の素材をまとめる',self.editor.consolidate_media))
        row.addWidget(self.button('保存フォルダ',lambda:self.open_folder('root')));layout.addLayout(row)
        self.add_page('media',page)

    def build_settings(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(32,28,32,28);layout.setSpacing(18)
        layout.addWidget(text('制作の設定','PageTitle'))
        storage,box=card();box.addWidget(text('動画の保存先','SectionTitle'))
        self.root_label=text(self.workspace.data['root'],'Muted');box.addWidget(self.root_label)
        box.addWidget(self.button('今後作る動画の保存先を変更',self.choose_root))
        box.addWidget(text('既存の動画は移動しません。動画一覧には、開いたフォルダが一度だけ登録されます。','Muted'));layout.addWidget(storage)
        updates,update_box=card();update_box.addWidget(text('アプリの更新','SectionTitle'))
        self.update_status=text('起動時に最新版を確認します。更新はバックグラウンドで取得します。','Muted')
        update_box.addWidget(self.update_status)
        self.update_progress=QProgressBar();self.update_progress.setRange(0,100);self.update_progress.setValue(0)
        self.update_progress.setFormat('%p%');self.update_progress.setTextVisible(True);self.update_progress.setFixedHeight(18)
        self.update_progress.hide();update_box.addWidget(self.update_progress)
        update_row=QHBoxLayout()
        self.update_check_button=self.button('更新を確認',self.editor.check_for_updates)
        self.update_download_button=self.button('更新をダウンロード',self.editor.download_update)
        self.update_install_button=self.button('更新して再起動',self.editor.install_update)
        self.update_download_button.setEnabled(False);self.update_install_button.setEnabled(False)
        update_row.addWidget(self.update_check_button);update_row.addWidget(self.update_download_button);update_row.addWidget(self.update_install_button);update_row.addStretch()
        update_box.addLayout(update_row)
        update_box.addWidget(text('更新ファイルを検証してから入れ替え、起動できない場合は前の版へ戻します。録画・配信・編集処理中は再起動できません。','Muted'))
        layout.addWidget(updates)
        layout.addStretch();self.add_page('settings',page)

    def navigate(self,key):
        if key!='live' and self._live and self._live.previewing and not self._live.engine.active:
            self._live.stop_preview()
        if key=='record' and self.current is None:return self.quick_record()
        if key not in ('home','settings','live','library') and self.current is None:
            self.status.setText('作品を選ぶか、企画・メモを作成してください。');key='library'
        if self._live and self._live.busy and key=='edit':
            self.status.setText('配信・録画素材の保存が完了してから編集できます。');key='live'
        if key=='live':
            if self._live is None:
                from .live_panel import LivePanel
                self._live=LivePanel(self);self.add_page('live',self._live)
            self._live.load_secret()
            if not self._live.busy:self.capture_badge.setText('配信停止中')
        if self._planning:self._planning.flush()
        if key=='plan':
            if self._planning is None:
                from .planning import PlanningPanel
                self._planning=PlanningPanel(self);self.add_page('plan',self._planning)
            self._planning.load(self.current)
        if key=='edit':
            if self._recording_panel and self._recording_panel.recorder.active:
                self.status.setText('収録を停止すると、素材を編集できます。');key='record'
            else:
                surface=self.editor.load_project(self.current)
                if surface is None:return False
                if 'edit' not in self.pages:self.add_page('edit',surface)
        if key=='record':self.recording_panel.refresh()
        elif key=='vtuber':self.vtuber
        elif key=='media':self.refresh_media()
        elif key=='library':self.refresh_projects()
        self.current_page=key
        self.stack.setCurrentWidget(self.pages[key])
        for name,button in self.nav.items():button.setChecked(name==key)
        self.editor.page_changed(key)
        self.update_header();return True

    def selected_project(self):
        if self.current is None:raise ValueError('先に動画を選択してください。')
        if not Path(self.current['folder']).is_dir():raise ValueError('動画フォルダが見つかりません。元のフォルダを開き直してください。')
        return self.current

    def select(self,project,remember=True):
        if not Path(project['folder']).is_dir():raise ValueError('動画フォルダが見つかりません。')
        if project is self.current:return True
        if self.busy:
            self.status.setText('収録や素材の準備が終わるまで、この動画で作業してください。');return False
        if not self.editor.save_before_switch():return False
        if self._planning:self._planning.flush()
        self.current=project
        if remember:self.workspace.remember(project)
        self.refresh_projects()
        if self._recording_panel:self._recording_panel.refresh()
        if self.current_page=='plan':self._planning.load(project)
        if self.current_page=='edit':self.editor.load_project(project)
        elif self.current_page=='media':self.refresh_media()
        self.update_header();return True

    def selected_projects(self):
        rows=sorted({index.row() for index in self.projects.selectionModel().selectedRows()})
        return [self.visible_projects[row] for row in rows if 0<=row<len(self.visible_projects)]

    def update_delete_button(self,*_):
        if not hasattr(self,'delete_button'):return
        selected_count=len(self.selected_projects())
        self.delete_button.setEnabled(selected_count>0 and not self.busy)
        self.delete_button.setText(f'選択した作品 {selected_count}件をゴミ箱へ' if selected_count>1 else '選択した作品をゴミ箱へ')

    def select_project(self,*_):
        row=self.projects.currentRow()
        if 0<=row<len(self.visible_projects):self.protect(lambda:self.select(self.visible_projects[row]))

    def refresh_projects(self,*_):
        from .planning import summary_for
        query=self.search.text().strip().casefold()
        selected_ids={self.visible_projects[index.row()]['id'] for index in self.projects.selectionModel().selectedRows()
                      if 0<=index.row()<len(self.visible_projects)} if hasattr(self,'projects') else set()
        self.visible_projects=[p for p in self.workspace.available_projects() if query in p['title'].casefold() or query in summary_for(p).get('title','').casefold()]
        self.projects.blockSignals(True);self.projects.setRowCount(len(self.visible_projects))
        for row,project in enumerate(self.visible_projects):
            info=summary_for(project)
            self.projects.setItem(row,0,QTableWidgetItem(info.get('title') or project['title']))
            date=QDateTime.fromString(info.get('schedule',''),Qt.DateFormat.ISODate)
            self.projects.setItem(row,1,QTableWidgetItem(date.toString('yyyy/MM/dd HH:mm') if date.isValid() else '未定'))
            self.projects.setItem(row,2,QTableWidgetItem('メモの読込エラー' if info.get('error') else '概要あり' if info.get('hasSummary') else 'アイデア'))
            updated=project.get('updated','')[:10].replace('-','.')
            self.projects.setItem(row,3,QTableWidgetItem(updated or '—'))
            index=self.projects.model().index(row,0)
            if project['id'] in selected_ids:
                self.projects.selectionModel().select(index,QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
            if self.current and project['id']==self.current['id']:
                self.projects.selectionModel().setCurrentIndex(index,QItemSelectionModel.SelectionFlag.NoUpdate)
        self.projects.blockSignals(False)
        self.empty.setVisible(not self.visible_projects)
        self.update_delete_button()

    def refresh_existing_folders(self):
        if self.current and not self.busy:
            if self.current_page=='media':self.protect(self.refresh_media)
            if self.current_page=='record' and self._recording_panel:self._recording_panel.refresh()
        if self.current_page=='library' and not self.busy:
            from .planning import summary_for
            query=self.search.text().strip().casefold()
            available=[p for p in self.workspace.available_projects() if query in p['title'].casefold() or query in summary_for(p).get('title','').casefold()]
            if available!=self.visible_projects:self.refresh_projects()

    def delete_project(self):
        return self.editor.delete_project(self.selected_project())

    def update_header(self):
        title=self.current['title'] if self.current and self.current_page not in ('home','live') else 'たぬつな Studio'
        self.project_heading.setText(title)
        self.context.setText({'home':'','library':'作品一覧','live':'YouTubeライブ','plan':'企画・自由メモ','record':'画面・PC音・マイク・V体','media':'PROJECT MEDIA','vtuber':'AVATAR','edit':'VIDEO EDITOR','settings':'PREFERENCES'}.get(self.current_page,''))
        contextual=self.current is not None and self.current_page not in ('home','live')
        for item in (self.rename_button,self.save_label,self.save_button):item.setVisible(contextual)
        self.context.setVisible(self.current_page!='home')
        self.capture_badge.setVisible(self.current_page in ('record','live') or self.busy)
        self.rename_button.setEnabled(self.current is not None);self.save_button.setEnabled(self.editor.loaded_editor is not None)
        for button in (self.continue_button,self.plan_button,self.record_button,self.media_button):button.setEnabled(self.current is not None)
        if self.current:
            from .planning import summary_for
            info=summary_for(self.current)
            self.hero_title.setText(info.get('title') or self.current['title'])
            count=len(takes_for(self.current))
            self.continue_button.setText('編集を続ける' if count else '録画へ進む' if info.get('hasSummary') else '企画・メモを開く')
            self.summary.setText(f'収録 {count}件  ·  1080p / 50fps\n収録と編集内容はこの動画に保存されます。')
        else:
            self.hero_title.setText('ここから、次の一本を。')
            self.summary.setText('動画を選ぶと、素材も編集内容も一緒に開けます。')
        self.editor.refresh_save_state()

    def create_project(self):
        if self.busy:raise ValueError('収録・素材の準備を完了してから作成してください。')
        title,ok=QInputDialog.getText(self,'新しい企画','企画名（仮で大丈夫です）')
        if not ok:return
        try:project=self.workspace.create(title)
        except DuplicateProject as exc:
            self.search.setText(exc.project['title']);self.select(exc.project);self.navigate('home')
            self.status.setText('同じ名前の動画がありました。続きを開けるよう選択しています。');return
        self.search.clear();self.select(project);self.navigate('plan')
        self.status.setText('企画を作成しました。まず自由メモ、次に企画の概要をまとめます。')

    def rename_project(self):
        project=self.selected_project();title,ok=QInputDialog.getText(self,'動画名を変更','動画名',text=project['title'])
        if ok:
            previous=project['title'];self.workspace.rename(project,title)
            surface=self.editor.loaded_editor
            if surface and surface.path==str(self.workspace.library_path(project)) and surface.sequence.name==previous:
                surface.commit('動画名を変更',lambda:setattr(surface.sequence,'name',title.strip()))
            if self._planning and self._planning.project is project:self._planning.title.setText(title.strip())
            self.refresh_projects();self.update_header()

    def continue_project(self):
        from .planning import summary_for
        project=self.selected_project()
        if takes_for(project):return self.open_editor()
        return self.navigate('record' if summary_for(project).get('hasSummary') else 'plan')

    def open_editor(self):return self.navigate('edit')
    def open_recording(self):return self.navigate('record')

    def refresh_media(self):
        if not self.current:return
        rows=[];seen=set();labels={'screen':'画面映像','desktop':'PC音','voice':'マイク','vtuber':'V体映像','bgm':'BGM','sfx':'効果音','yukkuri':'旧音声素材','transcript':'旧テキスト素材','subtitles':'字幕データ'}
        for folder,take in takes_for(self.current):
            if take.get('state')!='ready':continue
            for role,file in take['files'].items():
                if role not in labels:continue
                path=(folder/file).resolve()
                if not path.is_file():continue
                seen.add(str(path))
                rows.append((f'{folder.name[:15]} · {labels[role]}',labels[role],'配置済み' if take.get('imported') else '未配置',('take',folder,take,role)))
        root=Path(self.current['folder']);library=self.workspace.library_path(self.current)
        if library.is_file():
            with closing(sqlite3.connect(library.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                for path, in db.execute('SELECT path FROM assets'):
                    path=Path(path);path=path if path.is_absolute() else (library.parent/path).resolve()
                    if str(path) in seen or not path.is_file():continue
                    seen.add(str(path));rows.append((path.name,'素材','この動画内' if path.is_relative_to(root) else '外部参照',('file',str(path))))
        from cutraft.importer import MEDIA_EXTENSIONS
        for path in (root/'02-assets').glob('*'):
            if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS and '.partial.' not in path.name and str(path.resolve()) not in seen:
                rows.append((re.sub(r'-[a-f0-9]{12}(?=\.)','',path.name),'素材','未配置',('file',str(path.resolve()))))
        for path in (root/'04-exports').glob('*'):
            if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
                rows.append((path.name,'完成動画','書き出し済み',('file',str(path.resolve()))))
        self.media.setRowCount(len(rows))
        for row,(name,kind,state,data) in enumerate(rows):
            item=QTableWidgetItem(name);item.setData(Qt.ItemDataRole.UserRole,data);self.media.setItem(row,0,item)
            self.media.setItem(row,1,QTableWidgetItem(kind));self.media.setItem(row,2,QTableWidgetItem(state))

    def place_media(self):
        selected=sorted({i.row() for i in self.media.selectedIndexes()})
        if not selected:raise ValueError('編集に置く素材を選んでください。')
        if len(selected)==1:
            data=self.media.item(selected[0],0).data(Qt.ItemDataRole.UserRole)
            if data[0]=='take' and data[3] in ('transcript','subtitles'):
                self.navigate('record')
                row=next((i for i,(folder,_) in enumerate(self.recording_panel.entries) if folder==data[1]),-1)
                if row>=0:self.recording_panel.list.setCurrentRow(row)
                return
        takes=set();files=[]
        for row in selected:
            data=self.media.item(row,0).data(Qt.ItemDataRole.UserRole)
            if data[0]=='take':
                if str(data[1]) not in takes:
                    self.editor.import_recording(self.current,data[1],data[2],show=False);takes.add(str(data[1]))
            else:files.append(data[1])
        if files:self.editor.import_files(files)
        else:self.open_editor()

    def sync_takes(self):
        self.selected_project()
        self.editor.load_project(self.current,sync=True)
        self.open_editor()

    def project_menu(self,point):
        index=self.projects.indexAt(point)
        menu=QMenu(self)
        if index.isValid():
            self.projects.selectRow(index.row())
            project=self.visible_projects[index.row()]
            menu.addAction('編集を開く',lambda:self.protect(self.open_editor))
            menu.addAction('名前を変更',lambda:self.protect(self.rename_project))
            menu.addSeparator()
            action=menu.addAction('動画を削除（ゴミ箱へ）',lambda:self.protect(lambda:self.editor.delete_project(project)))
            action.setEnabled(not self.busy)
        menu.addAction('ゴミ箱を開く',self.editor.show_trash)
        menu.exec(self.projects.viewport().mapToGlobal(point))

    def media_menu(self,point):
        index=self.media.indexAt(point);menu=QMenu(self)
        if index.isValid():
            if not any(i.row()==index.row() for i in self.media.selectedIndexes()):self.media.selectRow(index.row())
            menu.addAction('編集に置く',lambda:self.protect(self.place_media))
            action=menu.addAction('選択した素材を削除（ゴミ箱へ）',lambda:self.protect(self.delete_selected_media))
            action.setEnabled(not self.busy)
        menu.addAction('ゴミ箱を開く',self.editor.show_trash)
        menu.exec(self.media.viewport().mapToGlobal(point))

    def delete_selected_media(self):
        rows=sorted({i.row() for i in self.media.selectedIndexes()})
        if not rows:return
        paths=[]
        for row in rows:
            data=self.media.item(row,0).data(Qt.ItemDataRole.UserRole)
            paths.append(str((Path(data[1])/data[2]['files'][data[3]]).resolve()) if data[0]=='take' else data[1])
        return self.editor.delete_data(paths=paths,label=self.media.item(rows[0],0).text()+(f' ほか{len(rows)-1}件' if len(rows)>1 else ''))

    def open_folder(self,kind):
        root=Path(self.selected_project()['folder'])
        if kind!='root':root/=FOLDERS[kind]
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def choose_root(self):
        folder=QFileDialog.getExistingDirectory(self,'今後作る動画の保存先',self.workspace.data['root'])
        if folder:
            self.workspace.data['root']=folder;self.workspace.save();self.root_label.setText(folder)

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction()
    def dropEvent(self,event):
        paths=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        self.protect(lambda:self.editor.import_files(paths));event.acceptProposedAction()

    def shutdown(self):
        if self._planning:self._planning.flush()
        if self._live:self._live.shutdown()
        self.folder_timer.stop()
        if self._recording_panel:self._recording_panel.shutdown()
        if self._vtuber:self._vtuber.shutdown()
