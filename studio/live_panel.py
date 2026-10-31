"""One preview and one start/stop control for window-only YouTube production."""
from pathlib import Path
from datetime import datetime
import time
import copy
import os
from PySide6.QtCore import Qt,QTimer,QSize,QRect,QUrl,QMicrophonePermission
from PySide6.QtGui import QImage,QPainter,QColor,QPixmap,QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
 QLineEdit,QCheckBox,QFormLayout,QDoubleSpinBox,QProgressBar,QFileDialog,QScrollArea,QApplication)
from PySide6.QtMultimedia import QWindowCapture,QVideoSink,QMediaCaptureSession,QMediaDevices
from .live_engine import LiveEngine,destination,stream_settings,ROLES
from .live_audio import LiveAudio
from .jobs import Jobs


class LivePanel(QWidget):
    def __init__(self,desk):
        super().__init__(desk);self.desk=desk;self.engine=LiveEngine(self);self.audio=None;self.preparing=False;self.previewing=False;self.pending_start=False
        self.camera_owned=False;self.game=None;self.renderer=None;self.cached_avatar=None;self.target=None;self.audio_ready=False;self.project=None;self.record_started=False;self.tick_count=0
        self.jobs=Jobs(self);self.jobs.finished.connect(self.saved)
        from .window_source import NativeWindowSource
        self.native_source=NativeWindowSource(self);self.native_source.listed.connect(self.show_windows);self.native_source.frame.connect(self.received_native);self.native_source.failed.connect(self.capture_error)
        self.capture=QWindowCapture(self)
        self.session=QMediaCaptureSession(self);self.sink=QVideoSink(self);self.session.setWindowCapture(self.capture);self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self.received);self.capture.errorOccurred.connect(self.capture_error)
        outer=QVBoxLayout(self);outer.setContentsMargins(24,18,24,18)
        title=QLabel('ライブ配信');title.setObjectName('PageTitle');outer.addWidget(title)
        top=QHBoxLayout();self.windows=QComboBox();self.windows.setPlaceholderText('取得するゲームのウィンドウ');self.windows.setMinimumWidth(220);self.windows.currentIndexChanged.connect(self.choose_window);self.windows.currentIndexChanged.connect(self.update_buttons)
        refresh=QPushButton('ウィンドウを更新');refresh.clicked.connect(self.refresh_windows)
        self.quality=QComboBox();self.quality.addItem('標準 1080p / 60fps',(1920,1080));self.quality.addItem('軽量 720p / 60fps',(1280,720));self.quality.setCurrentIndex(1)
        for widget in (self.windows,refresh,self.quality):top.addWidget(widget)
        outer.addLayout(top)
        body=QHBoxLayout();self.preview=QLabel('配信開始を押すと、必要な入力を自動で準備します。映像を確認したいときだけプレビューを使ってください。');self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(450,280);self.preview.setStyleSheet('background:#13232c;color:#d4e5eb;border-radius:10px');body.addWidget(self.preview,3)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setMinimumWidth(310);controls=QWidget();settings=QVBoxLayout(controls)
        self.avatar=QCheckBox('V体を重ねる');self.avatar.setChecked(True);settings.addWidget(self.avatar)
        from .camera_choice import CameraChoice
        self.camera_choice=CameraChoice(self);settings.addWidget(QLabel('V体を動かすカメラ'));settings.addWidget(self.camera_choice)
        form=QFormLayout();self.transforms={}
        for key,title,value in [('x','左右の位置',80),('y','上下の位置',75),('size','V体の大きさ',25)]:
            field=QDoubleSpinBox();field.setRange(0,100) if key!='size' else field.setRange(10,60);field.setValue(value);field.setSuffix(' %');self.transforms[key]=field;form.addRow(title,field)
        settings.addLayout(form)
        microw=QHBoxLayout();microw.addWidget(QLabel('マイク'));self.mics=QComboBox();self.mics.addItem('標準のマイク',b'')
        for device in QMediaDevices.audioInputs():self.mics.addItem(device.description(),bytes(device.id()))
        microw.addWidget(self.mics);settings.addLayout(microw)
        self.gains={};self.mutes={};self.meters={}
        for role,title,initial in [('desktop','PC全体',75),('voice','マイク',100),('bgm','BGM',20),('sfx','効果音',75)]:
            row=QHBoxLayout();row.addWidget(QLabel(title));gain=QDoubleSpinBox();gain.setRange(0,100);gain.setValue(initial);gain.setSuffix(' %')
            mute=QCheckBox('消音');row.addWidget(gain);row.addWidget(mute);settings.addLayout(row)
            meter=QProgressBar();meter.setRange(0,100);meter.setTextVisible(False);meter.setFixedHeight(8);settings.addWidget(meter)
            self.gains[role]=gain;self.mutes[role]=mute;self.meters[role]=meter
            gain.valueChanged.connect(self.update_mix);mute.toggled.connect(self.update_mix)
        self.bgm=QComboBox();self.bgm.addItem('BGMなし','');settings.addWidget(self.bgm)
        add=QPushButton('BGMのMP3候補を追加');add.clicked.connect(self.add_bgm);settings.addWidget(add)
        row=QHBoxLayout();play=QPushButton('BGMを再生');play.clicked.connect(self.play_bgm);stop=QPushButton('BGMを停止');stop.clicked.connect(lambda:self.engine.files['bgm'].close());row.addWidget(play);row.addWidget(stop);settings.addLayout(row)
        effect=QPushButton('効果音を選んで流す');effect.clicked.connect(self.play_effect);settings.addWidget(effect)
        settings.addStretch();scroll.setWidget(controls);body.addWidget(scroll,2);outer.addLayout(body,1)
        self.record=QCheckBox('同時に録画して、終了後に編集へ保存する');self.record.setChecked(False);outer.addWidget(self.record)
        saved=desk.workspace.data.get('live',{});form=QFormLayout();saved_server=saved.get('server')
        # Older releases rewrote Studio's plain-RTMP URL to RTMPS. Restore the
        # exact URL shown in Studio for that known default so TLS is not forced.
        if saved_server in ('rtmps://a.rtmps.youtube.com:443/live2','rtmps://a.rtmps.youtube.com/live2'):
            saved_server='rtmp://a.rtmp.youtube.com:1935/live2'
        self.server=QLineEdit(saved_server or 'rtmp://a.rtmp.youtube.com:1935/live2')
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setPlaceholderText('YouTube Studioのストリームキー')
        self.key.textChanged.connect(self.update_buttons)
        self.remember=QCheckBox('キーをOSの資格情報ストアへ保存する');self.secret_loaded=False
        form.addRow('ストリームURL',self.server);form.addRow('ストリームキー',self.key);outer.addLayout(form)
        outer.addWidget(QLabel('Studioに表示されたURLをそのまま使います。暗号化する場合は鍵アイコンからRTMPS URLをコピーしてください。RTMPは暗号化されません。'))
        outer.addWidget(self.remember)
        row=QHBoxLayout();self.preview_button=QPushButton('映像を確認');self.preview_button.setToolTip('先に取得するゲームのウィンドウを選択してください。プレビューにYouTubeキーは不要です。');self.preview_button.clicked.connect(lambda:desk.protect(self.prepare_preview));row.addWidget(self.preview_button)
        self.start=QPushButton('YouTubeへ配信開始（自動準備）');self.start.setObjectName('Primary');self.start.setToolTip('ゲームのウィンドウとYouTube Studioのストリームキーが必要です。');self.start.clicked.connect(lambda:desk.protect(self.begin));row.addWidget(self.start)
        self.stop=QPushButton('停止');self.stop.setEnabled(False);self.stop.clicked.connect(self.stop_all);row.addWidget(self.stop)
        self.elapsed=QLabel('停止中');row.addWidget(self.elapsed);row.addStretch();studio=QPushButton('YouTube Studio');studio.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://studio.youtube.com/')));row.addWidget(studio);outer.addLayout(row)
        status_row=QHBoxLayout();self.status=QLabel('映像は選択したウィンドウだけです。PC全体の音には、通知や他のアプリの音も含まれます。');self.status.setWordWrap(True);status_row.addWidget(self.status,1)
        self.copy_connection_detail=QPushButton('接続詳細をコピー');self.copy_connection_detail.setVisible(False)
        self.copy_connection_detail.clicked.connect(lambda:QApplication.clipboard().setText(self.connection_detail_text));status_row.addWidget(self.copy_connection_detail);outer.addLayout(status_row)
        self.transport_state='idle';self.performance_overloaded=False;self.last_avatar_render=0
        self.connection_detail_text=''
        self.engine.state.connect(self.state_changed);self.engine.connection_detail.connect(self.connection_detail_changed);self.engine.levels.connect(self.show_levels);self.engine.finished.connect(self.finished)
        # Compose each source frame at the fixed 60fps output cadence.
        self.timer=QTimer(self);self.timer.setTimerType(Qt.TimerType.PreciseTimer);self.timer.setInterval(16);self.timer.timeout.connect(self.tick)
        self.monitor=QTimer(self);self.monitor.setInterval(1000);self.monitor.timeout.connect(self.monitor_window);self.monitor.start()
        self.refresh_windows();self.update_mix();self.update_buttons()
        if self.windows.currentData() is None:self.status.setText('先にゲームのウィンドウを選択してください。映像確認にYouTubeキーは不要です。')

    @property
    def busy(self):return self.preparing or self.engine.active or self.jobs.busy

    def load_secret(self):
        if self.secret_loaded:return
        self.secret_loaded=True
        if self.key.text():return
        try:
            from .credentials import stream_key
            value=stream_key('get')
            if not self.key.text():self.key.setText(value);self.remember.setChecked(bool(value))
        except OSError:self.status.setText('保存したキーを取得できません。必要なら今回だけ入力して配信できます。')

    def refresh_windows(self):
        if self.native_source.available:
            self.native_source.enumerate();return
        from .window_source import windows_fallback
        windows=QWindowCapture.capturableWindows()
        self.show_windows([*windows,*windows_fallback(windows)])

    def show_windows(self,windows):
        previous=self.target;self.windows.blockSignals(True);self.windows.clear()
        windows=sorted(windows,key=lambda w:('minecraft' not in w.description().casefold(),w.description().casefold()))
        for window in windows:
            if getattr(window,'pid',None)==os.getpid():continue
            if not self.native_source.available and 'tanutsuna studio' in window.description().lower():continue
            self.windows.addItem(window.description() or 'タイトルなしのウィンドウ',window)
        index=next((i for i in range(self.windows.count()) if self.windows.itemData(i)==previous),-1)
        if index<0 and previous is not None and previous.isValid():
            # Some PySide wheels omit QCapturableWindow's value equality. Retain
            # the original native handle; never reselect by title or list position.
            self.windows.insertItem(0,'選択中: '+previous.description(),previous);index=0
        if previous is None and self.windows.count():index=0
        self.windows.setCurrentIndex(index);self.windows.blockSignals(False)
        if self.target is None and index>=0:self.target=self.windows.currentData()

    def choose_window(self,*_):
        window=self.windows.currentData()
        if window is None:return
        self.target=window;self.game=None
        if self.previewing:self.stop_capture();self.start_capture(window)

    def monitor_window(self):
        if not self.previewing:return
        if self.target is None or not self.target.isValid():
            self.stop_capture()
            self.game=None;self.status.setText('選択したゲーム画面を待っています。画面全体には切り替えません。音声は設定どおり継続します。')
        elif not self.capture.isActive() and not self.native_source.active:self.start_capture(self.target)

    def stop_capture(self):
        self.capture.stop();self.native_source.stop()

    def start_capture(self,window):
        from .window_source import NativeWindow
        if isinstance(window,NativeWindow):
            width,height=self.quality.currentData();self.native_source.start(window,width,height)
        else:self.capture.setWindow(window);self.capture.start()

    def received_native(self,image):
        if self.previewing and self.native_source.active:self.game=image

    def capture_error(self,*args):
        self.stop_capture();self.game=None
        self.status.setText(args[0] if args and isinstance(args[0],str) else 'ウィンドウの映像を取得できません。選択したゲームの起動状態と画面収録の許可を確認してください。')

    def received(self,frame):
        if self.previewing and self.capture.isActive() and frame.isValid():self.game=frame.toImage()

    def prepare_preview(self):
        if self.previewing:return
        window=self.windows.currentData()
        if window is None:
            self.status.setText('映像確認の前に、取得するゲームのウィンドウを選択してください。YouTubeキーはプレビューには不要です。')
            return False
        self.load_secret()
        if self.desk.busy:raise ValueError('収録・素材の処理を完了してから配信を準備してください。')
        app=QApplication.instance();permission=QMicrophonePermission()
        voice_available=bool(QMediaDevices.audioInputs())
        if voice_available:
            status=app.checkPermission(permission)
            if status==Qt.PermissionStatus.Undetermined:
                app.requestPermission(permission,self,self.permission_finished);return
            if status==Qt.PermissionStatus.Denied:
                if not self.mutes['voice'].isChecked():raise ValueError('マイクの許可がありません。OSの設定で許可するか、マイクを消音にしてください。')
                voice_available=False
        self.target=window;self.preparing=True;self.previewing=True;self.deadline=time.monotonic()+60
        self.last_avatar_render=0;self.timer.start()
        try:
            if self.avatar.isChecked():
                panel=self.desk.vtuber
                if not panel.server.state.snapshot()[1]:self.camera_owned=True;panel.start_tracking(automatic=True)
                from .avatar_offline import OfflineAvatar
                self.renderer=OfflineAvatar(panel.viewport.model,size=384 if self.quality.currentData()[0]==1280 else 512,samples=1)
            self.audio=LiveAudio(self.engine,self);self.audio.ready.connect(self.on_audio_ready);self.audio.failed.connect(self.input_failed)
            self.audio_ready=False
            self.audio.start(self.mics.currentData(),desktop=True,voice=voice_available)
            self.mutes['voice'].setEnabled(voice_available)
            if not voice_available:self.mutes['voice'].setChecked(True)
            self.start_capture(window);self.status.setText('ゲーム画面・V体・音声を準備中。OSの許可が出たら確認してください。まだ配信していません。')
        except Exception:
            self.stop_preview();raise
        self.update_buttons()

    def permission_finished(self,_status):
        try:self.prepare_preview()
        except Exception as exc:
            self.pending_start=False;self.update_buttons();self.status.setText(str(exc))

    def on_audio_ready(self):self.audio_ready=True

    def begin(self):
        if self.engine.active or self.pending_start:return
        if self.windows.currentData() is None:
            self.status.setText('配信開始の前に、取得するゲームのウィンドウを選択してください。')
            return False
        self.load_secret()
        try:server,key=stream_settings(self.server.text(),self.key.text())
        except ValueError as exc:
            self.status.setText(str(exc));return False
        self.server.setText(server);self.key.setText(key)
        self.project=None
        self.connection_detail_text='';self.copy_connection_detail.setVisible(False)
        # Keep the intent across the asynchronous OS microphone permission
        # prompt. Previously the method returned there without resuming publish.
        self.pending_start=True;self.start.setEnabled(False)
        try:
            if not self.previewing:self.prepare_preview()
        except Exception:
            self.pending_start=False;self.update_buttons();raise
        if self.pending_start:self.status.setText('ゲーム画面・カメラ・音声を自動準備中です。準備でき次第YouTubeへ接続します。プレビュー操作は不要です。')
        self.update_buttons()

    def launch(self):
        try:
            from .credentials import stream_key
            if self.remember.isChecked():stream_key('set',self.key.text())
            elif self.secret_loaded:stream_key('delete')
        except OSError:
            self.pending_start=False;self.status.setText('キーをOSへ保存できませんでした。保存のチェックを外すと今回だけ配信できます。');self.update_buttons();return
        width,height=self.quality.currentData()
        self.desk.workspace.data['live']={'server':self.server.text()};self.desk.workspace.save()
        self.pending_start=False
        if self.record.isChecked():
            if not self.desk.editor.save_before_switch():raise ValueError('編集中の作品を保存できなかったため配信を開始していません。')
            self.project=self.desk.workspace.create('ライブ録画 '+datetime.now().strftime('%Y-%m-%d %H-%M-%S'),allow_duplicate=True)
            if not self.desk.select(self.project):raise ValueError('録画の保存先を選択できません。')
        self.engine.start(destination(self.server.text(),self.key.text()),width,height,self.project,self.avatar.isChecked())
        self.desk.capture_badge.setText('配信へ接続中');self.update_buttons()

    def input_failed(self,message):
        if self.engine.active:
            self.status.setText(message+' 配信を停止します。録画素材は保持します。');self.stop_all()
        else:self.stop_preview();self.status.setText(message)

    def tick(self):
        for role,source in self.engine.files.items():
            if source.error:self.status.setText(('BGM: ' if role=='bgm' else '効果音: ')+source.error);source.error=''
        if not self.previewing:return
        camera_ready=not self.avatar.isChecked() or bool(self.desk.vtuber.server.state.camera_label)
        if self.preparing:
            if self.game is not None and camera_ready and self.audio_ready:
                self.preparing=False;self.status.setText('プレビュー準備完了。配信前に画面と音量を確認してください。');self.update_buttons()
            elif time.monotonic()>self.deadline:
                self.stop_preview();self.status.setText('入力の準備が完了しなかったため配信は開始していません。ゲーム・カメラ・音声の許可を確認してください。');return
        width,height=self.quality.currentData();screen=QImage(width,height,QImage.Format.Format_RGB32);screen.fill(QColor('#13232c'))
        painter=QPainter(screen)
        if self.game is not None:
            scaled=self.game.scaled(QSize(width,height),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation)
            painter.drawImage((width-scaled.width())//2,(height-scaled.height())//2,scaled)
        else:
            painter.setPen(QColor('#ffffff'));painter.drawText(screen.rect(),Qt.AlignmentFlag.AlignCenter,'ゲーム画面を待っています')
        painter.end();program=screen.copy() if self.renderer and self.avatar.isChecked() else screen;pose={};transform={k:v.value()/100 for k,v in self.transforms.items()}
        if self.renderer and self.avatar.isChecked():
            panel=self.desk.vtuber;pose=copy.deepcopy(panel.pose.__dict__);transform['orbit']=panel.orbit.value()
            try:
                now=time.monotonic()
                if self.cached_avatar is None or now-self.last_avatar_render>=1/30:
                    self.cached_avatar=self.renderer.frame(panel.pose,now,panel.orbit.value())
                    self.last_avatar_render=now
                avatar=self.cached_avatar
                painter=QPainter(program);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                side=round(width*transform['size']);rect=QRect(round(width*transform['x']-side/2),round(height*transform['y']-side/2),side,side)
                painter.drawImage(rect,avatar);painter.end()
            except Exception:self.input_failed('V体の描画を継続できません。軽量設定かV体オフで再試行してください。');return
        self.engine.submit_frame(program,screen,pose,transform);self.tick_count+=1
        if self.tick_count%3==0 and self.isVisible():
            self.preview.setPixmap(QPixmap.fromImage(program).scaled(self.preview.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation))
            if not self.engine.active:
                sources={**self.engine.inputs,**{k:v.ring for k,v in self.engine.files.items()}}
                self.show_levels({k:(0 if self.mutes[k].isChecked() else getattr(v,'peak',0)*self.gains[k].value()/100) for k,v in sources.items()})
        if self.pending_start and not self.preparing:
            try:self.launch()
            except Exception as exc:
                self.pending_start=False;self.status.setText(str(exc));self.update_buttons()
        if self.engine.active:
            elapsed=int(time.monotonic()-self.engine.started_at);self.elapsed.setText(f'{elapsed//3600:02}:{elapsed//60%60:02}:{elapsed%60:02}')

    def update_mix(self,*_):
        for role in ROLES:self.engine.set_mix(role,self.gains[role].value()/100,self.mutes[role].isChecked())

    def show_levels(self,levels):
        for role,value in levels.items():self.meters[role].setValue(min(100,round(value*100)))

    def add_bgm(self):
        files,_=QFileDialog.getOpenFileNames(self,'BGM候補を選択','','MP3 (*.mp3)')
        known={self.bgm.itemData(i) for i in range(self.bgm.count())}
        for file in files:
            if file not in known:self.bgm.addItem(Path(file).name,file)

    def play_bgm(self):
        file=self.bgm.currentData()
        if file:self.engine.files['bgm'].play(file,repeat=True)

    def play_effect(self):
        file,_=QFileDialog.getOpenFileName(self,'流す効果音を選択','','音声 (*.mp3 *.wav *.m4a *.flac)')
        if file:self.engine.files['sfx'].play(file)

    def state_changed(self,state):
        if not self.engine.active:return
        transport={'connecting':'YouTubeへ接続中。URLとキーを使って接続しています。','live':'YouTubeの受信先へ送信を開始しました。Studio側にプレビューが出るまで少し待ってください。','connection_error':'YouTubeへ接続できません。ストリームURL・キー・配信枠の開始・ネット接続を確認してください。自動再接続します。','reconnecting':'YouTubeへ再接続中です。ストリームURL・キー・ネット接続を確認してください。'}
        if state=='overload':
            self.performance_overloaded=True
            self.desk.capture_badge.setText('映像処理が遅延')
            self.desk.capture_badge.setStyleSheet('color:white;background:#b7283b;padding:10px;font-weight:700')
        elif state=='recovered':
            self.performance_overloaded=False
            self.desk.capture_badge.setText({'connecting':'接続中','live':'送信開始','connection_error':'接続を確認','reconnecting':'再接続中'}.get(self.transport_state,'配信中'))
            self.desk.capture_badge.setStyleSheet('')
        elif state in transport:
            self.transport_state=state
            if state=='live':self.connection_detail_text='';self.copy_connection_detail.setVisible(False)
            self.desk.capture_badge.setText({'connecting':'接続中','live':'送信開始','connection_error':'接続できません','reconnecting':'再接続中'}[state])
            if not self.performance_overloaded:self.desk.capture_badge.setStyleSheet('')
        else:return
        message=transport.get(self.transport_state,'配信準備中')
        if self.connection_detail_text and self.transport_state in ('connection_error','reconnecting','connecting'):
            message+=' 詳細: '+self.connection_detail_text
        if self.performance_overloaded:message+=' 映像処理にも遅れがあります。'
        self.status.setText(message)

    def connection_detail_changed(self,detail):
        self.connection_detail_text=detail
        self.copy_connection_detail.setVisible(bool(detail))
        if self.transport_state in ('connection_error','reconnecting','connecting'):
            self.state_changed(self.transport_state)

    def update_buttons(self):
        locked=self.engine.active or self.preparing or self.pending_start or self.jobs.busy
        has_window=self.windows.currentData() is not None
        has_key=bool(self.key.text().strip())
        self.start.setEnabled(not locked and has_window and has_key);self.preview_button.setEnabled(not locked and not self.previewing and has_window)
        self.stop.setEnabled(self.previewing or self.engine.active or self.pending_start)
        for widget in (self.quality,self.avatar,self.mics,self.camera_choice):widget.setEnabled(not locked and not self.previewing)
        for widget in (self.record,self.server,self.key,self.remember):widget.setEnabled(not locked)

    def stop_preview(self):
        self.preparing=False;self.pending_start=False;self.previewing=False;self.timer.stop();self.stop_capture();self.game=None
        if self.audio:self.audio.stop();self.audio=None
        if self.renderer:self.renderer.close();self.renderer=None
        self.cached_avatar=None
        if self.camera_owned and self.desk._vtuber:self.desk._vtuber.stop_tracking()
        self.camera_owned=False;self.mutes['voice'].setEnabled(True)
        for source in self.engine.files.values():source.close()
        self.update_buttons()

    def stop_all(self):
        self.pending_start=False
        if self.engine.active:
            self.engine.stop();self.status.setText('配信を停止し、録画があれば元素材を確定しています。');self.stop.setEnabled(False)
        else:self.stop_preview();self.status.setText('配信していません。プレビューを停止しました。')

    def finished(self,folder,take,error):
        self.stop_preview();self.desk.capture_badge.setText('配信停止中');self.desk.capture_badge.setStyleSheet('');self.elapsed.setText('停止中')
        if error:self.status.setText(error)
        if folder and take and not error:
            from .postprocess import finalize
            self.desk.capture_badge.setText('録画素材を保存中')
            self.jobs.submit('live-finalize',lambda cancel:finalize(folder,take,cancel));self.status.setText('配信を停止しました。分離した録画素材を保存し、編集へ配置します。')
        elif not error:self.status.setText('配信を停止しました。録画はオフのため素材は保存していません。')
        self.update_buttons()

    def saved(self,tag,take,error):
        if error:self.status.setText('録画の整理が未完了です。収録一覧から再開できます。 '+error)
        else:
            from .takes import takes_for
            folder=next((f for f,t in takes_for(self.project) if t['id']==take['id']),None)
            placed=folder and self.desk.protect(lambda:self.desk.editor.import_recording(self.project,folder,take,show=False))
            self.status.setText('画面・音声は保存・配置済みです。V体は未完了のため録画一覧から映像化を再開してください。' if placed and take.get('avatar_pending') else '配信の録画素材を保存し、編集へまとめて配置しました。' if placed else '録画素材は保存済みです。自動配置が完了しなかったため、素材一覧から編集に配置してください。')
        self.desk.capture_badge.setText('配信停止中')
        self.update_buttons()

    def can_close(self):
        if self.engine.active or self.jobs.busy:
            self.status.setText('配信を停止し、録画の保存が完了してから終了してください。');return False
        self.stop_preview();return True

    def shutdown(self):
        self.stop_preview();self.timer.stop();self.monitor.stop();self.engine.shutdown();self.jobs.shutdown()
