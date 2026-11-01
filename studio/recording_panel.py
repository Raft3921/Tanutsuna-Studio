from pathlib import Path
import json
import re
import sys
import time
from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QGuiApplication, QDesktopServices
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QListWidget, QSplitter, QMenu, QProgressBar)
from .recording import Recorder
from .jobs import Jobs
from .takes import takes_for, publish_take, recover_captures
from .postprocess import finalize, render_avatar_take


class RecordingPanel(QWidget):
    def __init__(self, desk):
        super().__init__(desk)
        self.desk = desk
        self.recorder = Recorder(self)
        self.jobs = Jobs(self)
        self.folder = self.take = self.project = None
        self.motion = None
        self.awaiting_camera=False;self.owns_camera=False;self.pending_options=None
        self.entries = []
        self.refreshing = False
        saved_settings=self.desk.workspace.data.get('preferences',{}).get('recording',{})
        layout = QVBoxLayout(self);layout.setContentsMargins(26,24,26,22);layout.setSpacing(14)
        heading = QLabel('録画');heading.setObjectName('PageTitle');layout.addWidget(heading)
        title = QLabel('画面全体を1080p・50fpsで収録。PC音とマイクは別々のMP3へ保存します。')
        title.setWordWrap(True); layout.addWidget(title)
        row = QHBoxLayout(); self.displays = QComboBox()
        for index, screen in enumerate(QGuiApplication.screens()): self.displays.addItem(f'{index+1}: {screen.name()} {screen.size().width()}×{screen.size().height()}', index)
        preferred_display=saved_settings.get('display_name','')
        if preferred_display:
            display_index=self.displays.findText(preferred_display)
            if display_index>=0:self.displays.setCurrentIndex(display_index)
        row.addWidget(QLabel('画面')); row.addWidget(self.displays, 1)
        self.mics = QComboBox(); self.mics.addItem('標準のマイク', '')
        for device in QMediaDevices.audioInputs(): self.mics.addItem(device.description(), bytes(device.id()).decode(errors='replace'))
        preferred_mic=saved_settings.get('mic_id','')
        mic_index=next((i for i in range(self.mics.count()) if self.mics.itemData(i)==preferred_mic),0)
        self.mics.setCurrentIndex(mic_index)
        row.addWidget(QLabel('マイク')); row.addWidget(self.mics, 1); layout.addLayout(row)
        row = QHBoxLayout()
        self.desktop = QCheckBox('ゲーム・PC音'); self.desktop.setChecked(bool(saved_settings.get('desktop',True)))
        self.voice = QCheckBox('マイク'); self.voice.setChecked(bool(saved_settings.get('voice',True)))
        self.avatar = QCheckBox('V体も別素材に保存'); self.avatar.setChecked(bool(saved_settings.get('avatar',True)))
        for widget in (self.desktop, self.voice, self.avatar): row.addWidget(widget)
        layout.addLayout(row)
        note_text=('V体のカメラは収録開始時に自動準備します。Windowsではカメラ・マイクの許可ダイアログは表示されないため、Windowsのプライバシー設定でデスクトップアプリのアクセスをオンにしてください。画面取得とPC音にもOSの許可画面はありません。' if sys.platform=='win32' else 'V体のカメラは収録開始時に自動準備します（初回のOS許可は必要です）。')
        note = QLabel(note_text+' 収録中は動きだけを記録し、映像化は終了後に処理し、全素材を編集の右端へ追加します。PC音には通知なども含みます。')
        note.setWordWrap(True); layout.addWidget(note)
        row = QHBoxLayout()
        self.start = QPushButton('収録を開始'); self.start.setObjectName('Primary'); self.start.clicked.connect(self.begin)
        self.stop = QPushButton('停止して保存'); self.stop.setEnabled(False); self.stop.clicked.connect(self.stop_or_cancel)
        self.elapsed = QLabel('停止中  00:00'); self.elapsed.setStyleSheet('font-size:24px;font-weight:700;padding:10px'); row.addWidget(self.start); row.addWidget(self.stop); row.addWidget(self.elapsed); row.addStretch()
        layout.addLayout(row)
        self.status = QLabel('動画を選び、「収録を開始」を押してください。'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.avatar_progress=QProgressBar();self.avatar_progress.setRange(0,100);self.avatar_progress.setValue(0)
        self.avatar_progress.setFormat('%p%');self.avatar_progress.setTextVisible(True);self.avatar_progress.setFixedHeight(18)
        self.avatar_progress.hide();layout.addWidget(self.avatar_progress)
        self.warnings = QLabel(); self.warnings.setWordWrap(True); self.warnings.setStyleSheet('color:#9b4500'); layout.addWidget(self.warnings)
        splitter = QSplitter()
        left = QWidget(); left.setObjectName('Card'); ll = QVBoxLayout(left);ll.setContentsMargins(16,18,16,18)
        ll.addWidget(QLabel('この動画の完成した収録'))
        self.list = QListWidget(); self.list.currentRowChanged.connect(self.select_take); ll.addWidget(self.list)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.take_menu)
        self.edit = QPushButton('素材をまとめて編集に配置'); self.edit.clicked.connect(self.open_editor); ll.addWidget(self.edit)
        self.retry = QPushButton('未完了の整理・映像化を再開'); self.retry.clicked.connect(self.retry_finalize); ll.addWidget(self.retry)
        self.retry.hide()
        self.upgrade_avatar=QPushButton('V体を高画質・透過で作り直す');ll.addWidget(self.upgrade_avatar)
        self.upgrade_avatar.clicked.connect(lambda:self.report(lambda:self.start_avatar(force=True)))
        folder = QPushButton('収録フォルダ'); folder.clicked.connect(self.open_folder); ll.addWidget(folder)
        remove=QPushButton('選択した収録を削除');remove.clicked.connect(lambda:self.report(lambda:self.desk.editor.delete_data(take_folders=[self.folder],label='選択した収録')) if self.folder else None);ll.addWidget(remove)
        splitter.addWidget(left)
        layout.addWidget(splitter, 1)
        settings=QPushButton('V体の見た目・カメラ設定');settings.clicked.connect(lambda:self.desk.navigate('vtuber'));layout.addWidget(settings)
        self.recorder.changed.connect(self.state_changed)
        self.recorder.progress.connect(self.message)
        self.recorder.started.connect(self.capture_started)
        self.recorder.stopped.connect(self.capture_stopped)
        self.recorder.failed.connect(self.capture_failed)
        self.jobs.finished.connect(self.job_finished)
        self.clock = QTimer(self); self.clock.setInterval(1000); self.clock.timeout.connect(self.show_elapsed)
        self.pose_timer = QTimer(self); self.pose_timer.setInterval(40); self.pose_timer.timeout.connect(self.save_pose)
        self.progress_timer = QTimer(self);self.progress_timer.setInterval(500);self.progress_timer.timeout.connect(self.show_save_progress)
        self.camera_timer=QTimer(self);self.camera_timer.setInterval(150);self.camera_timer.timeout.connect(self.check_camera)
        self.preferences_timer=QTimer(self);self.preferences_timer.setSingleShot(True);self.preferences_timer.setInterval(400);self.preferences_timer.timeout.connect(self.save_preferences)
        self.displays.currentIndexChanged.connect(self.schedule_preferences_save);self.mics.currentIndexChanged.connect(self.schedule_preferences_save)
        for widget in (self.desktop,self.voice,self.avatar):widget.toggled.connect(self.schedule_preferences_save)
        self.update_buttons()

    def schedule_preferences_save(self,*_):
        self.preferences_timer.start()

    def save_preferences(self):
        preferences=self.desk.workspace.data.setdefault('preferences',{})
        preferences['recording']={
            'display_name':self.displays.currentText(), 'mic_id':self.mics.currentData() or '',
            'desktop':self.desktop.isChecked(), 'voice':self.voice.isChecked(),
            'avatar':self.avatar.isChecked(),
        }
        self.desk.workspace.save()

    def show_save_progress(self):
        if not self.folder or not self.jobs.busy:return
        file=self.folder/'avatar-render.log'
        try:
            with file.open('rb') as stream:
                stream.seek(max(0,file.stat().st_size-4096));tail=stream.read().decode('utf-8',errors='replace')
            rows=re.findall(r'^([0-9.]+)/([0-9.]+)$',tail,re.MULTILINE)
            if rows:
                current,total=map(float,rows[-1])
                self.avatar_progress.setValue(max(0,min(100,round(100*current/max(total,1)))))
                self.avatar_progress.show()
                self.message(f'透過V体映像を保存中：{current:.0f} / {total:.0f} 秒。元の収録は保存済みです。')
        except OSError:pass

    def message(self, message): self.status.setText(str(message))

    def report(self, fn):
        try: return fn()
        except Exception as exc: self.message(exc); return None

    @property
    def busy(self): return self.awaiting_camera or self.recorder.active or self.jobs.busy

    def update_buttons(self):
        active, busy = self.recorder.active, self.busy
        self.start.setEnabled(not busy)
        self.stop.setEnabled(self.awaiting_camera or self.recorder.state in ('preparing','recording'))
        self.stop.setText('準備を中止' if self.awaiting_camera or self.recorder.state == 'preparing' else '停止して保存')
        for widget in (self.displays, self.mics, self.desktop, self.voice, self.avatar, self.list): widget.setEnabled(not busy)
        for widget in (self.edit, self.retry): widget.setEnabled(not busy and self.take is not None)
        self.retry.setVisible(bool(self.take and (self.take.get('state') != 'ready' or self.take.get('avatar_pending'))))
        if self.take: self.warnings.setText('\n'.join(self.take.get('warnings', [])))
        self.upgrade_avatar.setEnabled(not busy and self.folder is not None and
            'vtuber' not in self.take.get('deleted_roles',[]) and (self.folder/'vtuber-motion.jsonl').is_file())


    def begin(self):
        def start():
            if self.desk.busy: raise ValueError('素材の準備を完了してから収録してください。')
            self.project = self.desk.selected_project()
            editor = self.desk.editor.loaded_editor
            if editor:
                editor.preview.pause();editor.preview.timer.stop();editor.timeline.cache.suspended=True
            options = {'display': self.displays.currentData(), 'mic': self.mics.currentData(),
                'desktop': self.desktop.isChecked(), 'voice': self.voice.isChecked(), 'avatar': self.avatar.isChecked(),
                'transcribe': False}
            if options['avatar'] and not self.desk.vtuber.server.state.snapshot()[1]:
                self.pending_options=options;self.awaiting_camera=True;self.owns_camera=True
                self.camera_deadline=time.monotonic()+60
                try:self.desk.vtuber.start_tracking(automatic=True)
                except Exception:
                    self.cancel_camera();raise
                self.camera_timer.start();self.update_buttons()
                self.desk.capture_badge.setText('カメラの準備中')
                if sys.platform=='win32':
                    self.message('V体のカメラを自動準備中です。Windowsのプライバシー設定でデスクトップアプリのカメラアクセスをオンにしてください。許可ダイアログは表示されません。まだ収録は始まっていません。')
                else:
                    self.message('V体のカメラを自動準備中。初回はOSのカメラ許可を確認し、顔をカメラに向けてください。まだ収録は始まっていません。')
            else:self.launch_capture(options)
        self.report(start)

    def launch_capture(self,options):
        try:self.recorder.start(self.project,options)
        except Exception:
            self.release_camera();self.resume_editor();raise

    def release_camera(self):
        if self.owns_camera and self.desk._vtuber:self.desk._vtuber.stop_tracking()
        self.owns_camera=False

    def cancel_camera(self):
        self.camera_timer.stop();self.awaiting_camera=False;self.pending_options=None
        self.release_camera();self.resume_editor();self.update_buttons()
        self.desk.capture_badge.setText('収録停止中')

    def check_camera(self):
        if not self.awaiting_camera:return
        _,detected,message,_=self.desk.vtuber.server.state.snapshot()
        if detected:
            options=self.pending_options;self.camera_timer.stop();self.awaiting_camera=False;self.pending_options=None
            self.report(lambda:self.launch_capture(options));self.update_buttons()
        elif time.monotonic()>self.camera_deadline or message.startswith('開始できません'):
            self.cancel_camera()
            self.message('カメラを準備できなかったため収録は開始していません。V体のカメラ設定でOSの許可・接続・明るさを確認するか、V体をオフにして再試行してください。 '+message)

    def stop_or_cancel(self):
        if self.awaiting_camera:
            self.cancel_camera();self.message('カメラの準備を中止しました。');return
        if self.recorder.state == 'preparing': self.recorder.abort('収録の準備を中止しました。')
        else: self.recorder.stop()

    def state_changed(self, state):
        preparing=('収録の準備中です。Windows版では画面収録やマイクの許可ダイアログは表示されません。カメラ・マイクはWindowsのプライバシー設定でデスクトップアプリのアクセスをオンにしてください。' if sys.platform=='win32' else '収録の準備中。OSの画面収録・マイク許可が出たら確認してください。')
        names = {'preparing':preparing,
            'recording':'収録中 — 1080p / 50fps', 'stopping':'収録を停止してファイルを確定しています。'}
        if state in names: self.message(names[state])
        badge=self.desk.capture_badge
        badge.setText({'preparing':'収録の準備中','recording':'● 収録中','stopping':'停止・保存中'}.get(state,'収録停止中'))
        badge.setStyleSheet('color:white;background:#b7283b;font-weight:700;padding:10px' if state=='recording' else '')
        self.elapsed.setText({'preparing':'準備中','stopping':'停止・保存中'}.get(state,'停止中  00:00'))
        self.update_buttons()

    def capture_started(self, folder, take):
        self.folder, self.take = Path(folder), take
        if take['options']['avatar']:
            self.desk.vtuber.synced_recording = True
            self.motion = (self.folder / 'vtuber-motion.jsonl').open('w', encoding='utf-8', buffering=65536)
            self.pose_samples = 0
            self.motion.write(json.dumps({'format':'tanutsuna-synced-motion-v1', 'orbit':self.desk.vtuber.orbit.value()})+'\n')
            self.pose_epoch = self.recorder.epoch; self.save_pose(); self.pose_timer.start()
        self.clock.start(); self.show_elapsed()

    def save_pose(self):
        if self.motion:
            panel = self.desk.vtuber
            # Read only the latest smoothed pose; camera inference stays in the app.
            self.motion.write(json.dumps({'time':round(time.perf_counter()-self.pose_epoch,6), 'pose':panel.pose.__dict__}, separators=(',',':'))+'\n')
            self.pose_samples += 1
            if self.pose_samples % 25 == 0:self.motion.flush()

    def end_motion(self):
        if self.desk._vtuber:self.desk._vtuber.synced_recording = False
        self.pose_timer.stop()
        if self.motion:
            self.save_pose(); self.motion.close(); self.motion = None
        self.clock.stop();self.release_camera()

    def resume_editor(self):
        self.desk.editor.page_changed(self.desk.current_page)

    def capture_failed(self, message):
        self.end_motion(); self.resume_editor()
        self.folder, self.take = self.recorder.folder, self.recorder.take
        if message=='収録の準備・終了処理がタイムアウトしました。収録済みの元データは保持します。':
            self.message('収録の終了処理が時間内に完了しませんでした。元データは保持しています。「未完了の整理・映像化を再開」から続けられます。')
        else:self.message(message)
        self.refresh(); self.update_buttons()

    def capture_stopped(self, folder, take):
        self.end_motion()
        self.folder, self.take = publish_take(folder), take
        self.recorder.folder = self.folder
        self.submit_finalize()
        self.resume_editor()

    def show_elapsed(self):
        if self.recorder.state != 'recording': return
        seconds = max(0, int(time.perf_counter()-self.recorder.epoch))
        self.elapsed.setText(f'● 収録中  {seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}')
        self.desk.capture_badge.setText(f'● 収録中 {seconds//60:02}:{seconds%60:02}')

    def refresh(self):
        if self.busy: return
        self.refreshing = True
        previous = self.folder
        project = self.desk.current
        from .takes import ready_files
        if project: recover_captures(project)
        self.entries = [(f,t) for f,t in takes_for(project) if ready_files(f,t) or t.get('state') in ('captured','processing','incomplete')] if project else []
        self.list.clear()
        for folder, take in self.entries:
            state = {'ready':'編集可能','captured':'整理待ち','recording':'中断','preparing':'準備中断','processing':'処理中断','incomplete':'未完了'}.get(take['state'],take['state'])
            self.list.addItem(f'{folder.name}  {take.get("duration",0):.0f}秒  {state}')
        self.refreshing = False
        row = next((i for i,(folder,_) in enumerate(self.entries) if folder == previous), 0 if self.entries else -1)
        self.list.setCurrentRow(row)
        if row == -1: self.folder = self.take = None
        self.update_buttons()

    def select_take(self, row):
        if self.refreshing or self.busy or row < 0: return
        self.folder, self.take = self.entries[row]
        self.project = self.desk.current
        self.warnings.setText('\n'.join(self.take.get('warnings', [])))
        self.update_buttons()

    def submit_finalize(self):
        self.desk.capture_badge.setText('素材を保存中')
        self.message('元の収録を保存しました。MP3と透過V体映像をまとめて保存中です。長さに応じて時間がかかります。')
        folder, take = self.folder, self.take
        self.jobs.submit('finalize', lambda cancel: finalize(folder, take, cancel, render_avatar=True))
        self.progress_timer.start()
        self.update_buttons()

    def retry_finalize(self):
        if self.take and self.take.get('state') == 'ready' and self.take.get('avatar_pending'):
            self.report(self.start_avatar)
        else: self.report(self.submit_finalize)

    def job_finished(self, tag, result, error):
        self.desk.capture_badge.setText('収録停止中')
        self.progress_timer.stop()
        self.avatar_progress.hide()
        self.update_buttons()
        if error:
            self.message(error); self.refresh()
            if tag=='avatar' and self.desk.editor.loaded_editor:
                preview=self.desk.editor.loaded_editor.preview;preview.seek(preview.frame)
            return
        if tag == 'finalize':
            self.take = result
            if self.folder.parent.name=='.pending-recordings':
                self.folder=self.folder.parent.parent/'01-recordings'/self.folder.name
                self.recorder.folder=self.folder
            self.message('収録素材を保存しました。まとめて編集画面へ配置します。' if not self.take.get('avatar_error') else '映像と音声は保存済みです。V体は未完了のため「未完了の整理・映像化を再開」を押してください。')
            self.refresh()
            self.report(self.open_editor)
        elif tag == 'avatar':
            self.take = result
            self.report(self.open_editor)
            if self.desk.editor.loaded_editor:
                preview=self.desk.editor.loaded_editor.preview;preview.seek(preview.frame)
            self.message('V体映像を同期して追加しました。全素材を別トラックで編集できます。')
        self.update_buttons()

    def start_avatar(self,force=False):
        folder, take = self.folder, self.take
        if force and self.desk.editor.loaded_editor:
            preview=self.desk.editor.loaded_editor.preview
            old=str((folder/take['files'].get('vtuber','vtuber.mp4')).resolve())
            for channel in [preview.source_channel,*preview.channels.values()]:
                if channel.clip and preview.library.assets[channel.clip.asset_id].path==old:channel.clear()
        self.jobs.submit('avatar', lambda cancel: render_avatar_take(folder,take,cancel,force=force or take.get('avatar_pending',False)))
        self.avatar_progress.setValue(0);self.avatar_progress.show()
        self.progress_timer.start()
        self.message('映像・音声は編集できます。V体の別映像を作成中。完了後に自動で追加します。')
        self.update_buttons()

    def open_editor(self):
        def open_():
            if not self.take or self.take['state'] != 'ready': raise ValueError('先に収録素材の整理を完了してください。')
            return self.desk.editor.import_recording(self.project or self.desk.selected_project(), self.folder, self.take)
        return self.report(open_)

    def take_menu(self,point):
        item=self.list.itemAt(point);menu=QMenu(self)
        if item:
            row=self.list.row(item);self.list.setCurrentRow(row)
            folder,take=self.entries[row]
            menu.addAction('素材を編集に置く',self.open_editor)
            action=menu.addAction('この収録を削除（ゴミ箱へ）',lambda:self.report(lambda:self.desk.editor.delete_data(take_folders=[folder],label='収録 '+folder.name)))
            action.setEnabled(not self.desk.busy)
        menu.addAction('ゴミ箱を開く',self.desk.editor.show_trash)
        menu.exec(self.list.viewport().mapToGlobal(point))

    def open_folder(self):
        if self.folder: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))

    def can_close(self):
        if self.awaiting_camera:self.cancel_camera()
        if self.recorder.active:
            self.message('収録を停止してから終了してください。'); return False
        if self.jobs.busy and self.take and self.take.get('state') != 'ready':
            self.message('収録素材を保存しています。保存完了後に終了できます。'); return False
        if self.jobs.busy:
            self.jobs.cancel.set()
            # All source files and manifests are retained for resume on the next launch.
        return True

    def shutdown(self):
        self.preferences_timer.stop();self.save_preferences();self.camera_timer.stop();self.progress_timer.stop();self.end_motion(); self.jobs.shutdown()
