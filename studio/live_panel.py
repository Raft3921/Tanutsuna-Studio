"""One preview and one start/stop control for window-only YouTube production."""
from pathlib import Path
from datetime import datetime
import time
import copy
import os
import math
import sys
from PySide6.QtCore import Qt,QTimer,QSize,QRect,QUrl,QMicrophonePermission,QPointF
from PySide6.QtGui import (QImage,QPainter,QColor,QPixmap,QDesktopServices,QLinearGradient,
 QRadialGradient,QPen,QPainterPath,QBrush)
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
 QLineEdit,QCheckBox,QFormLayout,QDoubleSpinBox,QSlider,QProgressBar,QFileDialog,QScrollArea,QApplication,QFrame,QTabWidget,QMessageBox)
from PySide6.QtMultimedia import QWindowCapture,QVideoSink,QMediaCaptureSession,QMediaDevices,QMediaPlayer,QAudioOutput
from PySide6.QtGui import QMovie
from .live_engine import LiveEngine,destination,stream_settings,ROLES
from .live_audio import LiveAudio
from .jobs import Jobs


class LivePanel(QWidget):
    def __init__(self,desk):
        super().__init__(desk);self.desk=desk;self.engine=LiveEngine(self);self.audio=None;self.preparing=False;self.previewing=False;self.pending_start=False
        saved=desk.workspace.data.get('live',{});self.saved_preferences=saved.get('preferences',{})
        self.camera_owned=False;self.game=None;self._scaled_game=QImage();self._scaled_game_key=None;self.renderer=None;self.cached_avatar=None;self.target=None;self.audio_ready=False;self.project=None;self.record_started=False;self.tick_count=0
        self.jobs=Jobs(self);self.jobs.finished.connect(self.saved)
        from .window_source import NativeWindowSource
        self.native_source=NativeWindowSource(self);self.native_source.listed.connect(self.show_windows);self.native_source.frame.connect(self.received_native);self.native_source.failed.connect(self.capture_error)
        self.capture=QWindowCapture(self)
        self.session=QMediaCaptureSession(self);self.sink=QVideoSink(self);self.session.setWindowCapture(self.capture);self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self.received);self.capture.errorOccurred.connect(self.capture_error)
        self.setObjectName('LivePanel');self.setStyleSheet('''
            QWidget#LivePanel { background:#071621; }
            QTabWidget::pane { background:#0b1d29; border:1px solid #24424d; border-radius:10px; }
            QTabBar::tab { background:#102734; color:#9bb8c1; border:1px solid #23424e; border-bottom:0; padding:9px 13px; border-top-left-radius:8px; border-top-right-radius:8px; }
            QTabBar::tab:selected { background:#173440; color:#d8faf2; border-color:#44c9ad; }
            QGroupBox { color:#d9f3f4; background:#0c202b; border:1px dotted #2d6670; border-radius:10px; margin-top:12px; padding:10px; font-weight:700; }
            QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 6px; color:#73dfc7; }
            QWidget#LiveLayoutTab, QWidget#LiveChatTab, QWidget#LiveAudioTab { background:#081e29; color:#e4f4f7; }
            QWidget#LiveLayoutTab QLabel, QWidget#LiveChatTab QLabel, QWidget#LiveAudioTab QLabel { color:#d8edf1; background:transparent; }
            QWidget#LiveLayoutTab QCheckBox, QWidget#LiveChatTab QCheckBox, QWidget#LiveAudioTab QCheckBox { color:#e4f4f7; spacing:8px; }
            QWidget#LiveLayoutTab QCheckBox::indicator, QWidget#LiveChatTab QCheckBox::indicator, QWidget#LiveAudioTab QCheckBox::indicator { width:18px; height:18px; background:#102d38; border:2px solid #6c929c; border-radius:4px; }
            QWidget#LiveLayoutTab QCheckBox::indicator:checked, QWidget#LiveChatTab QCheckBox::indicator:checked, QWidget#LiveAudioTab QCheckBox::indicator:checked { background:#11b9a5; border:2px solid #b0fff0; }
            QWidget#LiveLayoutTab QComboBox, QWidget#LiveLayoutTab QDoubleSpinBox, QWidget#LiveChatTab QComboBox, QWidget#LiveChatTab QDoubleSpinBox, QWidget#LiveChatTab QLineEdit, QWidget#LiveAudioTab QComboBox, QWidget#LiveAudioTab QDoubleSpinBox { color:#153b48; background:#f8fcfd; }
            QWidget#LiveLayoutTab QPushButton, QWidget#LiveChatTab QPushButton, QWidget#LiveAudioTab QPushButton { color:#153b48; }
            QScrollArea#LiveControls { background:#081e29; border:1px solid #24424d; border-radius:12px; }
            QSlider::groove:horizontal { height:4px; background:#25434e; border-radius:2px; }
            QSlider::handle:horizontal { width:12px; margin:-5px 0; background:#65dfc2; border:1px solid #b4fff0; border-radius:6px; }
        ''')
        dot_tile=QPixmap(18,18);dot_tile.fill(Qt.GlobalColor.transparent)
        dot_painter=QPainter(dot_tile);dot_painter.setPen(Qt.PenStyle.NoPen);dot_painter.setBrush(QColor(238,255,190,72));dot_painter.drawEllipse(3,3,3,3);dot_painter.end()
        self.stream_dot_brush=QBrush(dot_tile)
        outer=QVBoxLayout(self);outer.setContentsMargins(24,18,24,18)
        title=QLabel('ライブ配信');title.setObjectName('PageTitle');outer.addWidget(title)
        top=QHBoxLayout();self.windows=QComboBox();self.windows.setPlaceholderText('取得するゲームのウィンドウ');self.windows.setMinimumWidth(220);self.windows.currentIndexChanged.connect(self.choose_window);self.windows.currentIndexChanged.connect(self.update_buttons)
        refresh=QPushButton('ウィンドウを更新');refresh.clicked.connect(self.refresh_windows)
        self.quality=QComboBox();self.quality.addItem('標準 1080p / 60fps',(1920,1080));self.quality.addItem('軽量 720p / 60fps',(1280,720));self.quality.setCurrentIndex(max(0,min(1,int(self.saved_preferences.get('quality',1)))))
        self.background_frame=QImage();self.quality.currentIndexChanged.connect(self.prepare_background);self.prepare_background()
        for widget in (self.windows,refresh,self.quality):top.addWidget(widget)
        outer.addLayout(top)
        body=QHBoxLayout()
        scroll=QScrollArea();scroll.setObjectName('LiveControls');scroll.setWidgetResizable(True);scroll.setMinimumWidth(285);scroll.setMaximumWidth(390);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);controls=QWidget();settings=QVBoxLayout(controls)
        self.control_tabs=QTabWidget();settings.addWidget(self.control_tabs)
        layout_tab=QWidget();layout_tab.setObjectName('LiveLayoutTab');layout_box=QVBoxLayout(layout_tab);self.control_tabs.addTab(layout_tab,'● レイアウト')
        chat_tab=QWidget();chat_tab.setObjectName('LiveChatTab');chat_box=QVBoxLayout(chat_tab);self.control_tabs.addTab(chat_tab,'● チャット');self.chat_tab=chat_tab
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.chat_view=QWebEngineView(chat_tab);self.chat_view.setMinimumHeight(200)
        audio_tab=QWidget();audio_tab.setObjectName('LiveAudioTab');audio_box=QVBoxLayout(audio_tab);self.control_tabs.addTab(audio_tab,'● 音声')
        self.avatar=QCheckBox('ラフトV体を表示');self.avatar.setChecked(bool(self.saved_preferences.get('avatar',True)));layout_box.addWidget(self.avatar)
        self.avatar_model=QComboBox();self.avatar_model.addItem('ラフトV体','raft');self.avatar_model.addItem('たぬつなV体','tanuki')
        self.avatar_model.setCurrentIndex(max(0,self.avatar_model.findData(desk.vtuber.avatar_model)))
        layout_box.addWidget(QLabel('使用するV体'));layout_box.addWidget(self.avatar_model)
        self.avatar_model.currentIndexChanged.connect(self.change_avatar_model)
        desk.vtuber.modelChanged.connect(self.sync_avatar_model)
        self.scenes_button=QPushButton('シーンを切り替える');self.scenes_button.clicked.connect(self.open_scenes_window);layout_box.addWidget(self.scenes_button)
        from .camera_choice import CameraChoice
        self.camera_choice=CameraChoice(self);layout_box.addWidget(QLabel('V体を動かすカメラ'));layout_box.addWidget(self.camera_choice)
        form=QFormLayout();self.transforms={};self.transform_sliders={}
        for key,title,value in [('x','左右の位置',80),('y','上下の位置',75),('size','大きさ',25),('orbit','左右の3D回転',0)]:
            field=QDoubleSpinBox();field.setRange(-90,90) if key=='orbit' else field.setRange(10,60) if key=='size' else field.setRange(0,100);field.setDecimals(1);field.setSingleStep(1 if key=='orbit' else .5);field.setValue(float(self.saved_preferences.get('transform_'+key,value)));field.setSuffix('°' if key=='orbit' else ' %');self.transforms[key]=field;form.addRow(title,field)
            slider=QSlider(Qt.Orientation.Horizontal);slider.setRange(-900,900) if key=='orbit' else slider.setRange(100,600) if key=='size' else slider.setRange(0,1000);slider.setSingleStep(5);slider.setPageStep(50);slider.setValue(round(field.value()*10));slider.setToolTip(f'{title}をスライダーで調整')
            slider.valueChanged.connect(lambda n, field=field: field.setValue(n/10))
            field.valueChanged.connect(lambda n, slider=slider: self.sync_transform_slider(slider,n))
            self.transform_sliders[key]=slider;form.addRow(slider)
        layout_group=QFrame();layout_group.setObjectName('LayoutCard');layout_box.addWidget(layout_group);layout_group.setLayout(form)
        self.game_transforms={};game_form=QFormLayout()
        for key,label,default in [('x','ゲーム左右',50),('y','ゲーム上下',50),('scale','ゲーム拡大',100)]:
            field=QDoubleSpinBox();field.setRange(0,100) if key!='scale' else field.setRange(30,180);field.setValue(float(self.saved_preferences.get('game_'+key,default)));field.setSuffix(' %');self.game_transforms[key]=field;game_form.addRow(label,field)
        game_group=QFrame();game_box=QVBoxLayout(game_group);game_box.addWidget(QLabel('● ゲーム画面'));game_box.addLayout(game_form);layout_box.addWidget(game_group)
        self.media_enabled=QCheckBox('追加素材を配信に表示');self.media_enabled.setChecked(bool(self.saved_preferences.get('media_enabled',False)));layout_box.addWidget(self.media_enabled)
        self.media_path=QLineEdit(str(self.saved_preferences.get('media_path','')));self.media_path.setReadOnly(True);self.media_path.setPlaceholderText('画像・GIF・動画を選択');layout_box.addWidget(self.media_path)
        media_buttons=QHBoxLayout();self.media_choose=QPushButton('素材を選ぶ');self.media_play=QPushButton('▶ 再生');self.media_pause=QPushButton('Ⅱ 一時停止');self.media_stop=QPushButton('■ 停止')
        for button in (self.media_choose,self.media_play,self.media_pause,self.media_stop):media_buttons.addWidget(button)
        layout_box.addLayout(media_buttons)
        self.media_transforms={};media_form=QFormLayout()
        for key,label,default in [('x','素材左右',80),('y','素材上下',75),('scale','素材の大きさ',28)]:
            field=QDoubleSpinBox();field.setRange(0,100) if key!='scale' else field.setRange(5,100);field.setValue(float(self.saved_preferences.get('media_'+key,default)));field.setSuffix(' %');self.media_transforms[key]=field;media_form.addRow(label,field)
        layout_box.addLayout(media_form);layout_box.addStretch()
        self.media_choose.clicked.connect(self.choose_media);self.media_play.clicked.connect(self.play_media);self.media_pause.clicked.connect(self.pause_media);self.media_stop.clicked.connect(self.stop_media)
        self.media_enabled.toggled.connect(self.schedule_preferences_save)
        self.media_player=QMediaPlayer(self);self.media_audio=QAudioOutput(self);self.media_player.setAudioOutput(self.media_audio);self.media_sink=QVideoSink(self);self.media_player.setVideoOutput(self.media_sink);self.media_sink.videoFrameChanged.connect(self.media_video_frame)
        self.media_audio.setVolume(.7);self.media_image=QImage();self.media_movie=None;self.media_type='';self.media_source=''
        self.opening_frame=QImage();self.opening_movie=QMovie(str(Path(__file__).parent/'assets'/'live-opening.gif'))
        self.opening_movie.setCacheMode(QMovie.CacheMode.CacheAll);self.opening_movie.frameChanged.connect(self.update_opening_frame)
        self.opening_movie.start()
        audio_toggle=QPushButton('＋ 音声・BGMの詳細設定');audio_toggle.setCheckable(True);audio_toggle.setChecked(False)
        audio_panel=QWidget();audio_settings=QVBoxLayout(audio_panel);audio_settings.setContentsMargins(0,0,0,0);audio_panel.hide()
        audio_toggle.toggled.connect(lambda shown:(audio_panel.setVisible(shown),audio_toggle.setText(('−' if shown else '＋')+' 音声・BGMの詳細設定')))
        audio_box.addWidget(audio_toggle)
        microw=QHBoxLayout();microw.addWidget(QLabel('マイク'));self.mics=QComboBox();self.mics.addItem('標準のマイク',b'')
        for device in QMediaDevices.audioInputs():self.mics.addItem(device.description(),bytes(device.id()))
        saved_mic=self.saved_preferences.get('mic_id','')
        mic_index=next((i for i in range(self.mics.count()) if bytes(self.mics.itemData(i) or b'').decode(errors='replace')==saved_mic),0)
        self.mics.setCurrentIndex(mic_index)
        microw.addWidget(self.mics);audio_settings.addLayout(microw)
        self.gains={};self.mutes={};self.meters={}
        for role,title,initial in [('desktop','PC全体',75),('voice','マイク',100),('bgm','BGM',20),('sfx','効果音',75)]:
            row=QHBoxLayout();row.addWidget(QLabel(title));gain=QDoubleSpinBox();gain.setRange(0,100);gain.setValue(float(self.saved_preferences.get('gain_'+role,initial)));gain.setSuffix(' %')
            mute=QCheckBox('消音');mute.setChecked(bool(self.saved_preferences.get('mute_'+role,False)));row.addWidget(gain);row.addWidget(mute);audio_settings.addLayout(row)
            meter=QProgressBar();meter.setRange(0,100);meter.setTextVisible(False);meter.setFixedHeight(8);audio_settings.addWidget(meter)
            self.gains[role]=gain;self.mutes[role]=mute;self.meters[role]=meter
            if role=='voice':
                self.voice_gate_indicator=QLabel();self.voice_gate_indicator.setStyleSheet('font-size:11px;color:#63818a');audio_settings.addWidget(self.voice_gate_indicator)
            gain.valueChanged.connect(self.update_mix);mute.toggled.connect(self.update_mix)
        self.bgm=QComboBox();self.bgm.addItem('BGMなし','')
        for file in self.saved_preferences.get('bgm_files',[]):
            if isinstance(file,str) and Path(file).is_file():self.bgm.addItem(Path(file).name,file)
        saved_bgm=self.saved_preferences.get('bgm_file','')
        if saved_bgm and Path(saved_bgm).is_file():self.bgm.setCurrentIndex(max(0,self.bgm.findData(saved_bgm)))
        audio_settings.addWidget(self.bgm)
        add=QPushButton('BGMのMP3候補を追加');add.clicked.connect(self.add_bgm);audio_settings.addWidget(add)
        row=QHBoxLayout();play=QPushButton('BGMを再生');play.clicked.connect(self.play_bgm);stop=QPushButton('BGMを停止');stop.clicked.connect(lambda:self.engine.files['bgm'].close());row.addWidget(play);row.addWidget(stop);audio_settings.addLayout(row)
        effect=QPushButton('効果音を選んで流す');effect.clicked.connect(self.play_effect);audio_settings.addWidget(effect)
        audio_box.addWidget(audio_panel)
        audio_box.addStretch();scroll.setWidget(controls);body.addWidget(scroll,2)
        stage=QFrame();stage.setObjectName('LiveProgramStage');stage.setStyleSheet('QFrame#LiveProgramStage{background:#071722;border:1px dotted #39717a;border-radius:16px}');stage_layout=QVBoxLayout(stage);stage_layout.setContentsMargins(16,14,16,16);stage_layout.setSpacing(10)
        stage_header=QHBoxLayout();stage_name=QLabel('LIVE  /  PROGRAM MONITOR');stage_name.setStyleSheet('font-size:13px;font-weight:750;letter-spacing:1px;color:#d4f5fa')
        self.scene_badge=QLabel('STANDBY');self.scene_badge.setObjectName('LiveSceneBadge');self.scene_badge.setStyleSheet('color:#b8dce5;background:#173341;padding:5px 11px;border:1px solid #2a5262;border-radius:10px;font-weight:700')
        self.fps_badge=QLabel('● 60 TARGET');self.fps_badge.setStyleSheet('color:#79e0be;background:#102d2c;padding:5px 10px;border:1px solid #236155;border-radius:10px;font-weight:700')
        stage_header.addWidget(stage_name);stage_header.addStretch();stage_header.addWidget(self.fps_badge);stage_header.addWidget(self.scene_badge);stage_layout.addLayout(stage_header)
        chat_row=QHBoxLayout();self.chat_overlay_enabled=QCheckBox('チャットを配信映像に表示');self.chat_overlay_enabled.setChecked(bool(self.saved_preferences.get('chat_overlay',False)))
        self.chat_url=QLineEdit(saved.get('video_url',''));self.chat_url.setPlaceholderText('YouTube配信URL  ·  APIキー不要');self.chat_url.setMaximumWidth(420)
        self.chat_status=QLabel('チャット OFF');self.chat_status.setStyleSheet('color:#86aab5;font-size:11px')
        self.stage_status=QLabel('● DOT LIVE');self.stage_status.setStyleSheet('color:#79e0be;font-size:11px;font-weight:700')
        chat_row.addWidget(self.stage_status);chat_row.addStretch();stage_layout.addLayout(chat_row)
        chat_box.addWidget(self.chat_overlay_enabled);chat_box.addWidget(self.chat_url)
        self.chat_names_enabled=QCheckBox('ユーザー名を表示');self.chat_names_enabled.setChecked(bool(self.saved_preferences.get('chat_usernames',True)));chat_box.addWidget(self.chat_names_enabled)
        self.chat_transforms={};chat_form=QFormLayout()
        for key,label,default,low,high in [('x','左右',83,0,100),('y','上下',44,0,100),('width','幅',24,10,50),('height','高さ',62,15,90),('opacity','不透明度',58,15,100)]:
            field=QDoubleSpinBox();field.setRange(low,high);field.setValue(float(self.saved_preferences.get('chat_'+key,default)));field.setSuffix(' %');self.chat_transforms[key]=field;chat_form.addRow(label,field)
        chat_box.addLayout(chat_form);chat_box.addWidget(self.chat_status);chat_box.addWidget(self.chat_view,1)
        self.preview=QLabel('配信開始前  ·  ゲーム画面と音声を確認できます');self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(480,270);self.preview.setStyleSheet('background:#08151e;color:#c3dbe3;border:1px dotted #39717a;border-radius:12px')
        stage_layout.addWidget(self.preview,1);body.addWidget(stage,5)
        from .raft_avatar import RaftAvatarView
        self.live_avatar=RaftAvatarView(self.preview);self.live_avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        self.tanuki_avatar=QLabel(self.preview);self.tanuki_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter);self.tanuki_avatar.setStyleSheet('background:transparent;border:none');self.tanuki_avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        self.tanuki_avatar.hide();self._renderer_error=''
        self.live_avatar.backendChanged.connect(lambda _software,message:self.status.setText('ラフトV体: '+message))
        self.live_avatar.setVisible(self.avatar.isChecked() and desk.vtuber.avatar_model=='raft')
        self.tanuki_avatar.setVisible(self.avatar.isChecked() and desk.vtuber.avatar_model=='tanuki')
        self.avatar.toggled.connect(self.update_avatar_visibility)
        self.chat_page=self.chat_view.page();self.chat_page.loadFinished.connect(self.chat_loaded)
        self.chat_messages=[];self.chat_page_ready=False
        self.chat_overlay_enabled.toggled.connect(self.chat_overlay_changed);self.chat_url.editingFinished.connect(self.load_chat_overlay)
        self.chat_capture_timer=QTimer(self);self.chat_capture_timer.setInterval(1000);self.chat_capture_timer.timeout.connect(self.refresh_chat_messages)
        if self.chat_overlay_enabled.isChecked():QTimer.singleShot(250,lambda:self.chat_overlay_changed(True))
        outer.addLayout(body,1)
        self.record=QCheckBox('同時に録画して、終了後に編集へ保存する');self.record.setChecked(bool(self.saved_preferences.get('record',False)));outer.addWidget(self.record)
        form=QFormLayout();saved_server=saved.get('server')
        # Older releases rewrote Studio's plain-RTMP URL to RTMPS. Restore the
        # exact URL shown in Studio for that known default so TLS is not forced.
        if saved_server in ('rtmps://a.rtmps.youtube.com:443/live2','rtmps://a.rtmps.youtube.com/live2'):
            saved_server='rtmp://a.rtmp.youtube.com:1935/live2'
        self.server=QLineEdit(saved_server or 'rtmp://a.rtmp.youtube.com:1935/live2')
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setPlaceholderText('YouTube Studioのストリームキー')
        self.key.textChanged.connect(self.update_buttons)
        self.remember=QCheckBox('キーをOSの資格情報ストアへ保存する');self.secret_loaded=False
        self.key.textEdited.connect(self.save_stream_secret)
        self.remember.toggled.connect(self.save_stream_secret)
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
        self.fps_epoch=time.monotonic();self.fps_frames=0;self.measured_fps=0
        self.scenes_window=None;self.current_scene='game';self.scene_transition=None;self.scene_started=0.
        self.connection_detail_text=''
        self.engine.state.connect(self.state_changed);self.engine.connection_detail.connect(self.connection_detail_changed);self.engine.levels.connect(self.show_levels);self.engine.finished.connect(self.finished)
        # Compose each source frame at the fixed 60fps output cadence.
        self.timer=QTimer(self);self.timer.setTimerType(Qt.TimerType.PreciseTimer);self.timer.setInterval(16);self.timer.timeout.connect(self.tick);self.timer.start()
        self.monitor=QTimer(self);self.monitor.setInterval(1000);self.monitor.timeout.connect(self.monitor_window);self.monitor.start()
        self.preferences_timer=QTimer(self);self.preferences_timer.setSingleShot(True);self.preferences_timer.setInterval(400);self.preferences_timer.timeout.connect(self.save_preferences)
        for widget in (self.windows,self.quality,self.mics,self.bgm):widget.currentIndexChanged.connect(self.schedule_preferences_save)
        for widget in (self.avatar,self.record,*self.mutes.values()):widget.toggled.connect(self.schedule_preferences_save)
        for widget in (*self.transforms.values(),*self.gains.values()):widget.valueChanged.connect(self.schedule_preferences_save)
        self.server.textChanged.connect(self.schedule_preferences_save)
        self.chat_overlay_enabled.toggled.connect(self.schedule_preferences_save);self.chat_url.editingFinished.connect(self.schedule_preferences_save)
        self.chat_names_enabled.toggled.connect(self.schedule_preferences_save)
        for field in (*self.game_transforms.values(),*self.media_transforms.values(),*self.chat_transforms.values()):field.valueChanged.connect(self.schedule_preferences_save)
        self.refresh_windows();self.update_mix();self.update_buttons();QTimer.singleShot(0,self.load_secret)
        if self.windows.currentData() is None:self.status.setText('先にゲームのウィンドウを選択してください。映像確認にYouTubeキーは不要です。')

    @property
    def busy(self):return self.preparing or self.engine.active or self.jobs.busy

    def showEvent(self,event):
        super().showEvent(event);self.timer.start()

    def hideEvent(self,event):
        super().hideEvent(event)
        if not self.previewing:self.timer.stop()

    def schedule_preferences_save(self,*_):
        self.preferences_timer.start()

    def change_avatar_model(self,*_):
        model=str(self.avatar_model.currentData() or 'raft')
        if self.desk.vtuber.avatar_model!=model:
            self.desk.vtuber.model_choice.setCurrentIndex(self.desk.vtuber.model_choice.findData(model))

    def sync_avatar_model(self,model):
        self.avatar_model.blockSignals(True);self.avatar_model.setCurrentIndex(max(0,self.avatar_model.findData(model)));self.avatar_model.blockSignals(False)
        if model=='raft' and self.renderer:
            self.renderer.close();self.renderer=None;self.cached_avatar=None;self._renderer_error=''
        self.update_avatar_visibility()

    def update_avatar_visibility(self,*_):
        scene_allows=self.current_scene!='intro' or bool(self.scene_transition and self.scene_transition['to']!='intro')
        enabled=self.avatar.isChecked() and scene_allows;model=self.desk.vtuber.avatar_model
        self.live_avatar.setVisible(enabled and model=='raft')
        self.tanuki_avatar.setVisible(enabled and model=='tanuki')
        if not enabled and not self.previewing:
            self.preview.clear();self.preview.setText('配信開始を押すと、必要な入力を自動で準備します。')

    def update_idle_avatar_preview(self,avatar,transform):
        if avatar is None or avatar.isNull():return
        width=max(1,self.preview.width());height=max(1,self.preview.height())
        frame=QImage(width,height,QImage.Format.Format_RGB32);frame.fill(QColor('#13232c'))
        if not self.background_frame.isNull():
            painter=QPainter(frame);painter.drawImage(0,0,self.background_frame);painter.end()
        side=round(width*transform['size'])
        rect=QRect(round(width*transform['x']-side/2),round(height*transform['y']-side/2),side,side)
        painter=QPainter(frame);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform);painter.drawImage(rect,avatar);painter.end()
        self.preview.setPixmap(QPixmap.fromImage(frame))

    def save_preferences(self):
        live=self.desk.workspace.data.setdefault('live',{})
        live['server']=self.server.text().strip()
        live['preferences']={
            'quality':self.quality.currentIndex(),'avatar':self.avatar.isChecked(),
            'chat_overlay':self.chat_overlay_enabled.isChecked(),
            'chat_usernames':self.chat_names_enabled.isChecked(),
            **{'chat_'+name:field.value() for name,field in self.chat_transforms.items()},
            **{'game_'+name:field.value() for name,field in self.game_transforms.items()},
            'media_enabled':self.media_enabled.isChecked(),'media_path':self.media_path.text(),
            **{'media_'+name:field.value() for name,field in self.media_transforms.items()},
            **{'transform_'+name:field.value() for name,field in self.transforms.items()},
            'mic_id':bytes(self.mics.currentData() or b'').decode(errors='replace'),
            **{'gain_'+role:gain.value() for role,gain in self.gains.items()},
            **{'mute_'+role:mute.isChecked() for role,mute in self.mutes.items()},
            'bgm_files':[self.bgm.itemData(i) for i in range(1,self.bgm.count()) if self.bgm.itemData(i)],
            'bgm_file':self.bgm.currentData() or '', 'record':self.record.isChecked(),
            'window_title':self.windows.currentText() if self.windows.currentData() is not None else '',
        }
        self.desk.workspace.save()

    def chat_overlay_changed(self,enabled):
        if enabled:
            self.control_tabs.setCurrentWidget(self.chat_tab)
            self.chat_status.setText('チャットページを読み込み中…');self.stage_status.setText('● CHAT LOADING');self.load_chat_overlay();self.chat_capture_timer.start()
        else:
            self.chat_capture_timer.stop();self.chat_status.setText('チャット OFF');self.stage_status.setText('● DOT LIVE')

    def load_chat_overlay(self):
        from .youtube_panel import video_id_from_url
        video_id=video_id_from_url(self.chat_url.text())
        if not video_id:
            if self.chat_overlay_enabled.isChecked():
                self.chat_status.setText('有効なYouTube URLを入力してください')
                self.status.setText('チャット表示には有効なYouTubeライブURLが必要です。APIキーは不要です。')
            return
        self.desk.workspace.data.setdefault('live',{})['video_url']=self.chat_url.text().strip();self.desk.workspace.save()
        if getattr(self,'chat_video_id','')!=video_id:
            self.chat_video_id=video_id
            self.chat_messages=[];self.chat_page_ready=False
            self.chat_status.setText('チャットページを読み込み中…')
            self.chat_page.setUrl(QUrl('https://www.youtube.com/live_chat?is_popout=1&dark_theme=1&v='+video_id))

    def chat_loaded(self,ok):
        if not self.chat_overlay_enabled.isChecked():return
        self.chat_page_ready=bool(ok)
        if not ok:
            self.chat_status.setText('チャットを読み込めません。配信URL・公開範囲・ネット接続を確認してください')
            self.stage_status.setText('● CHAT ERROR');return
        self.chat_status.setText('チャット接続済み・新着を待っています');self.stage_status.setText('● CHAT READY')
        self.refresh_chat_messages()

    def refresh_chat_messages(self):
        if not self.chat_overlay_enabled.isChecked() or not self.chat_page_ready:return
        script=r'''(()=>{
          const rows=[];const seen=new Set();
          const text=(root,selector)=>{
            if(!root)return '';
            const direct=root.querySelector(selector);
            if(direct)return (direct.innerText||direct.textContent||'').trim();
            for(const node of root.querySelectorAll('*'))if(node.shadowRoot){const value=text(node.shadowRoot,selector);if(value)return value}
            return '';
          };
          const walk=root=>{for(const node of root.querySelectorAll('*')){
            const tag=node.tagName.toLowerCase();
            if(/^yt-live-chat-(text-message-renderer|paid-message-renderer|paid-sticker-renderer|membership-item-renderer|ticker-paid-message-item-renderer|ticker-sponsor-item-renderer)$/.test(tag)&&!seen.has(node)){
              seen.add(node);const author=text(node,'#author-name');
              const message=text(node,'#message')||text(node,'#purchase-amount')||text(node,'#header-subtext');
              if(author||message)rows.push({author:author.slice(0,80),message:message.slice(0,500)});
            }
            if(node.shadowRoot)walk(node.shadowRoot);
            if(node.tagName==='IFRAME')try{if(node.contentDocument)walk(node.contentDocument)}catch(_e){}
          }};
          walk(document);return rows.slice(-10);
        })()'''
        self.chat_page.runJavaScript(script,self.chat_messages_updated)

    def chat_messages_updated(self,rows):
        if not self.chat_overlay_enabled.isChecked():return
        if not isinstance(rows,list):
            self.chat_status.setText('チャットページ接続済み・メッセージを読み取り中…')
            self.stage_status.setText('● CHAT READING');return
        messages=[]
        for row in rows[-10:]:
            if isinstance(row,dict):
                author=str(row.get('author','')).strip()[:80];message=str(row.get('message','')).strip()[:500]
                if author or message:messages.append({'author':author,'message':message})
        self.chat_messages=messages
        if messages:
            self.chat_status.setText(f'配信映像に表示中 · {len(messages)}件');self.stage_status.setText('● CHAT IN PROGRAM')
        elif self.chat_page_ready:
            self.chat_status.setText('チャット接続済み・新着を待っています');self.stage_status.setText('● CHAT READY')

    def paint_chat_overlay(self,frame):
        if not self.chat_overlay_enabled.isChecked():return
        width,height=frame.width(),frame.height();opts=self.chat_transforms
        box_w=max(100,round(width*opts['width'].value()/100));box_h=max(100,round(height*opts['height'].value()/100))
        cx=round(width*opts['x'].value()/100);cy=round(height*opts['y'].value()/100)
        x=max(0,min(width-box_w,cx-box_w//2));y=max(0,min(height-box_h,cy-box_h//2));rect=QRect(x,y,box_w,box_h)
        painter=QPainter(frame);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha=round(opts['opacity'].value()*2.55)
        painter.setPen(QPen(QColor(27,139,104,max(190,alpha)),max(2,round(width/800)),Qt.PenStyle.DotLine))
        painter.setBrush(QColor(241,248,220,min(246,alpha+100)));painter.drawRect(rect)
        painter.save();path=QPainterPath();path.addRect(QRect(x+5,y+5,box_w-10,box_h-10));painter.setClipPath(path)
        painter.fillRect(QRect(x+6,y+6,box_w-12,box_h-12),self.stream_dot_brush)
        base_font=painter.font();base_font.setPixelSize(max(16,round(width/105)))
        painter.setFont(base_font)
        header=QRect(x+6,y+6,box_w-12,36);painter.fillRect(header,QColor(166,223,105,245))
        painter.setPen(QColor('#153d36'));painter.drawText(QRect(x+14,y+8,box_w-28,30),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'● LIVE CHAT')
        messages=getattr(self,'chat_messages',[])[-10:]
        body=QRect(x+14,y+43,box_w-28,box_h-54)
        if not messages:
            painter.setPen(QColor('#51736a'));painter.drawText(body,Qt.AlignmentFlag.AlignCenter,'チャットを待っています…')
        else:
            row_h=max(48,body.height()//min(6,len(messages)));visible=messages[-max(1,body.height()//row_h):]
            for index,item in enumerate(visible):
                row_y=body.y()+index*row_h
                if index:painter.setPen(QPen(QColor(40,139,104,110),1,Qt.PenStyle.DotLine));painter.drawLine(x+14,row_y,rect.right()-14,row_y)
                cursor=row_y+5
                if self.chat_names_enabled.isChecked() and item['author']:
                    author_font=painter.font();author_font.setPixelSize(max(13,round(width/130)));painter.setFont(author_font)
                    painter.setPen(QColor('#247d5f'));painter.drawText(QRect(x+14,cursor,box_w-28,20),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,item['author']);cursor+=20
                    painter.setFont(base_font)
                painter.setPen(QColor('#173b43'));painter.drawText(QRect(x+14,cursor,box_w-28,max(20,row_h-(cursor-row_y)-4)),Qt.TextFlag.TextWordWrap,item['message'])
        painter.restore()
        painter.end()

    def update_opening_frame(self,*_):
        image=self.opening_movie.currentImage()
        if not image.isNull():self.opening_frame=image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def choose_media(self):
        path,_=QFileDialog.getOpenFileName(self,'配信に重ねる素材を選択','','画像・動画 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.mp4 *.mov *.m4v *.webm *.mkv)')
        if not path:return
        self.stop_media();self.media_source=path;self.media_path.setText(path);suffix=Path(path).suffix.lower()
        self.media_image=QImage();self.media_movie=None
        if suffix=='.gif':
            self.media_type='gif';movie=QMovie(path);movie.setCacheMode(QMovie.CacheMode.CacheAll)
            if not movie.isValid():QMessageBox.warning(self,'GIFを開けません','別のGIFファイルを選んでください。');self.media_type='';return
            movie.frameChanged.connect(lambda _frame,movie=movie:self.set_media_image(movie.currentImage()))
            self.media_movie=movie;movie.start()
        elif suffix in ('.png','.jpg','.jpeg','.webp','.bmp'):
            self.media_type='image';image=QImage(path)
            if image.isNull():QMessageBox.warning(self,'画像を開けません','別の画像ファイルを選んでください。');self.media_type='';return
            self.media_image=image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        else:
            self.media_type='video';self.media_player.setSource(QUrl.fromLocalFile(path));self.media_enabled.setChecked(True)
        if self.media_type in ('gif','image'):self.media_enabled.setChecked(True)
        self.schedule_preferences_save()

    def set_media_image(self,image):
        if not image.isNull():self.media_image=image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def media_video_frame(self,frame):
        if frame.isValid():self.set_media_image(frame.toImage())

    def play_media(self):
        if self.media_type=='video':self.media_player.play()
        elif self.media_movie:self.media_movie.start()

    def pause_media(self):
        if self.media_type=='video':self.media_player.pause()
        elif self.media_movie:self.media_movie.setPaused(True)

    def stop_media(self):
        if hasattr(self,'media_player'):self.media_player.stop()
        if getattr(self,'media_movie',None):self.media_movie.stop();self.set_media_image(self.media_movie.currentImage())
        elif getattr(self,'media_type','')=='video':self.media_image=QImage()

    def prepare_background(self,*_):
        width,height=self.quality.currentData()
        image=QImage(width,height,QImage.Format.Format_RGB32)
        gradient=QLinearGradient(0,0,width,height);gradient.setColorAt(0,QColor('#071724'));gradient.setColorAt(.52,QColor('#103642'));gradient.setColorAt(1,QColor('#111b39'))
        painter=QPainter(image);painter.fillRect(image.rect(),gradient);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow=QRadialGradient(QPointF(width*.72,height*.34),width*.72);glow.setColorAt(0,QColor(47,191,168,48));glow.setColorAt(1,QColor(47,191,168,0));painter.fillRect(image.rect(),glow)
        spacing=max(14,round(width/110));painter.setPen(Qt.PenStyle.NoPen)
        for y in range(spacing//2,height,spacing):
            for x in range(spacing//2,width,spacing):
                strength=max(0,1-math.hypot((x-width*.68)/(width*.85),(y-height*.4)/(height*.9)))
                painter.setBrush(QColor(120,235,218,round(12+22*strength)));painter.drawEllipse(QPointF(x,y),1.3+1.0*strength,1.3+1.0*strength)
        painter.setPen(QColor(116,228,221,28));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRoundedRect(QRect(28,28,width-56,height-56),24,24)
        painter.end();self.background_frame=image

    def open_scenes_window(self):
        if self.scenes_window is not None:
            self.scenes_window.show();self.scenes_window.raise_();self.scenes_window.activateWindow();return
        from .live_scenes import LiveScenesDialog
        dialog=LiveScenesDialog(self);dialog.sceneRequested.connect(self.switch_scene)
        dialog.destroyed.connect(lambda *_: setattr(self,'scenes_window',None))
        self.scenes_window=dialog;dialog.show()

    def switch_scene(self,scene):
        if scene not in ('intro','game','break'):return
        destination=self.scene_transition['to'] if self.scene_transition else self.current_scene
        if scene==destination:return
        if scene=='intro':
            now=time.monotonic();self.auto_game_after_intro=False;self.opening_movie.stop();self.opening_movie.jumpToFrame(0);self.opening_movie.start();self.stop_media()
            if self.current_scene=='intro':
                self.scene_started=now;self.scene_transition=None
            else:self.scene_started=now;self.scene_transition={'from':self.current_scene,'to':'intro','started':now}
            self.scene_badge.setText('OPENING');self.status.setText('オープニング演出中です。マイクはミュートされています。')
        elif scene==self.current_scene and self.scene_transition:
            self.scene_transition=None;self.auto_game_after_intro=False
            self.scene_badge.setText({'game':'LIVE','intro':'OPENING','break':'BRB'}[self.current_scene])
            self.status.setText('現在のシーンを続けます。')
        elif scene!=self.current_scene:
            self.auto_game_after_intro=False
            now=time.monotonic()
            if scene=='break':self.scene_started=now
            self.scene_transition={'from':self.current_scene,'to':scene,'started':now}
            self.scene_badge.setText('切替中')
            self.status.setText('シーンを切り替えています。切替中はマイクをミュートします。')
        self.update_mix()

    @staticmethod
    def sync_transform_slider(slider,value):
        slider.blockSignals(True);slider.setValue(round(value*10));slider.blockSignals(False)

    def load_secret(self):
        if self.secret_loaded:return
        self.secret_loaded=True
        if self.key.text():return
        try:
            from .credentials import stream_key
            value=stream_key('get')
            if not self.key.text():self.key.setText(value);self.remember.setChecked(bool(value))
        except OSError:self.status.setText('保存したキーを取得できません。必要なら今回だけ入力して配信できます。')

    def save_stream_secret(self,*_):
        try:
            if self.remember.isChecked() and self.key.text().strip():
                from .credentials import stream_key
                stream_key('set',self.key.text().strip());self.secret_loaded=True
            elif not self.remember.isChecked() and self.secret_loaded:
                from .credentials import stream_key
                stream_key('delete');self.secret_loaded=False
        except OSError:
            self.status.setText('ストリームキーを安全に保存できませんでした。OSのキーチェーン設定を確認してください。')

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
        if previous is None and self.windows.count():
            preferred=self.saved_preferences.get('window_title','')
            index=next((i for i in range(self.windows.count()) if self.windows.itemText(i)==preferred),0)
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
        if args and isinstance(args[0],str):self.status.setText(args[0])
        elif sys.platform=='win32':self.status.setText('選択したゲームの画面を取得できません。ゲームが起動中か、画面取得に対応したウィンドウか確認してください。Windowsでは画面収録の許可画面は表示されません。')
        else:self.status.setText('ウィンドウの映像を取得できません。選択したゲームの起動状態と画面収録の許可を確認してください。')

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
        # QPermission is not implemented for Windows desktop apps. Windows
        # controls them with the global desktop-app microphone switch; treating
        # the unsupported status as Denied blocks Windows preview startup.
        if voice_available and sys.platform=='darwin':
            status=app.checkPermission(permission)
            if status==Qt.PermissionStatus.Undetermined:
                app.requestPermission(permission,self,self.permission_finished);return
            if status==Qt.PermissionStatus.Denied:
                if not self.mutes['voice'].isChecked():raise ValueError('マイクの許可がありません。OSの設定で許可するか、マイクを消音にしてください。')
                voice_available=False
        self.target=window;self.preparing=True;self.previewing=True;self.deadline=time.monotonic()+60
        self.last_avatar_render=0;self.fps_epoch=time.monotonic();self.fps_frames=0;self.fps_badge.setText('● 計測中');self.timer.start()
        try:
            if self.avatar.isChecked():
                panel=self.desk.vtuber
                if panel.avatar_model=='tanuki' and self.renderer is None:
                    from .avatar_offline import OfflineAvatar
                    self.renderer=OfflineAvatar(panel.tanuki_view.model,size=384 if self.quality.currentData()[0]==1280 else 512,samples=1)
                    self._renderer_error=''
                if not panel.server.state.snapshot()[1]:self.camera_owned=True;panel.start_tracking(automatic=True)
            self.audio=LiveAudio(self.engine,self);self.audio.ready.connect(self.on_audio_ready);self.audio.failed.connect(self.input_failed)
            self.audio_ready=False
            self.audio.start(self.mics.currentData(),desktop=True,voice=voice_available)
            self.mutes['voice'].setEnabled(voice_available)
            if not voice_available:self.mutes['voice'].setChecked(True)
            preparing=('ゲーム画面・V体・音声を準備中です。Windowsでは許可ダイアログは表示されません。カメラとマイクのプライバシー設定でデスクトップアプリのアクセスを確認してください。' if sys.platform=='win32' else 'ゲーム画面・V体・音声を準備中。OSの許可が出たら確認してください。')
            self.start_capture(window);self.status.setText(preparing+' まだ配信していません。')
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
        live_settings=self.desk.workspace.data.setdefault('live',{});live_settings['server']=self.server.text();self.desk.workspace.save()
        self.pending_start=False
        if self.record.isChecked():
            if not self.desk.editor.save_before_switch():raise ValueError('編集中の作品を保存できなかったため配信を開始していません。')
            self.project=self.desk.workspace.create('ライブ録画 '+datetime.now().strftime('%Y-%m-%d %H-%M-%S'),allow_duplicate=True)
            if not self.desk.select(self.project):raise ValueError('録画の保存先を選択できません。')
        self.current_scene='intro';self.scene_transition=None;self.scene_started=time.monotonic();self.auto_game_after_intro=False
        self.opening_movie.stop();self.opening_movie.jumpToFrame(0);self.opening_movie.start();self.stop_media()
        self.scene_badge.setText('OPENING');self.update_mix()
        self.engine.start(destination(self.server.text(),self.key.text()),width,height,self.project,self.avatar.isChecked())
        self.desk.capture_badge.setText('配信へ接続中');self.update_buttons()

    def input_failed(self,message):
        if self.engine.active:
            self.status.setText(message+' 配信を停止します。録画素材は保持します。');self.stop_all()
        else:self.stop_preview();self.status.setText(message)

    def tick(self):
        if not self.previewing and not self.isVisible():return
        for role,source in self.engine.files.items():
            if source.error:self.status.setText(('BGM: ' if role=='bgm' else '効果音: ')+source.error);source.error=''
        panel=self.desk.vtuber
        transform={k:v.value()/100 for k,v in self.transforms.items()}
        model=panel.avatar_model
        avatar_image=None
        intro_hold=self.current_scene=='intro' and (self.scene_transition is None or self.scene_transition.get('to')=='intro')
        avatar_enabled=self.avatar.isChecked() and not intro_hold
        self.live_avatar.setVisible(avatar_enabled and model=='raft')
        self.tanuki_avatar.setVisible(avatar_enabled and model=='tanuki')
        if avatar_enabled and model=='raft':
            self.live_avatar.setVisible(True);self.tanuki_avatar.hide()
            self.live_avatar.orbit=transform['orbit']*100
            self.live_avatar.update_pose(panel.pose,time.monotonic())
            w=max(1,self.preview.width());h=max(1,self.preview.height());side=max(64,round(w*transform['size']))
            self.live_avatar.setGeometry(round(w*transform['x']-side/2),round(h*transform['y']-side/2),side,side)
            avatar_image=self.live_avatar.capture_frame()
        elif avatar_enabled and model=='tanuki':
            self.live_avatar.hide();self.tanuki_avatar.setVisible(True)
            w=max(1,self.preview.width());h=max(1,self.preview.height());side=max(64,round(w*transform['size']))
            self.tanuki_avatar.setGeometry(round(w*transform['x']-side/2),round(h*transform['y']-side/2),side,side)
            try:
                if self.renderer is None and not self._renderer_error:
                    from .avatar_offline import OfflineAvatar
                    self.renderer=OfflineAvatar(panel.tanuki_view.model,size=384 if self.quality.currentData()[0]==1280 else 512,samples=1)
                if self.renderer and (self.cached_avatar is None or time.monotonic()-self.last_avatar_render>=1/60):
                    self.cached_avatar=self.renderer.frame(panel.pose,time.monotonic(),transform['orbit']*100);self.last_avatar_render=time.monotonic()
                avatar_image=self.cached_avatar
                if self.cached_avatar:self.tanuki_avatar.setPixmap(QPixmap.fromImage(self.cached_avatar).scaled(side,side,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
            except Exception as exc:
                self._renderer_error=str(exc);self.status.setText('たぬつなV体を描画できません: '+self._renderer_error)
                if self.previewing:self.input_failed('たぬつなV体を描画できません。V体の設定でラフトV体に切り替えて再試行してください。');return
        if not self.previewing:
            self.update_idle_avatar_preview(avatar_image,transform)
            return
        camera_ready=not self.avatar.isChecked() or bool(self.desk.vtuber.server.state.camera_label)
        if self.preparing:
            if self.game is not None and camera_ready and self.audio_ready:
                self.preparing=False;self.status.setText('プレビュー準備完了。配信前に画面と音量を確認してください。');self.update_buttons()
            elif time.monotonic()>self.deadline:
                self.stop_preview();self.status.setText('入力の準備が完了しなかったため配信は開始していません。ゲーム・カメラ・音声の許可を確認してください。');return
        width,height=self.quality.currentData();screen=QImage(width,height,QImage.Format.Format_RGB32);screen.fill(QColor('#050b10'))
        painter=QPainter(screen)
        if self.game is not None:
            game_scale=self.game_transforms['scale'].value()/100
            key=(self.game.cacheKey(),width,height,game_scale)
            if key!=self._scaled_game_key:
                target=QSize(max(1,round(width*game_scale)),max(1,round(height*game_scale)))
                self._scaled_game=self.game.scaled(target,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation);self._scaled_game_key=key
            gx=round(width*self.game_transforms['x'].value()/100-self._scaled_game.width()/2);gy=round(height*self.game_transforms['y'].value()/100-self._scaled_game.height()/2)
            painter.drawImage(gx,gy,self._scaled_game)
        else:
            if not self.background_frame.isNull():painter.drawImage(0,0,self.background_frame)
            painter.setPen(QColor('#9bb9c2'));painter.drawText(QRect(0,round(height*.67),width,42),Qt.AlignmentFlag.AlignCenter,'GAME LINK  /  WAITING FOR WINDOW')
            painter.setPen(QColor('#dffaf4'));painter.drawText(QRect(0,round(height*.72),width,42),Qt.AlignmentFlag.AlignCenter,'ゲーム画面を待っています')
        painter.end();game_program=screen.copy() if (avatar_enabled or self.media_enabled.isChecked()) else screen;pose={};transform={k:v.value()/100 for k,v in self.transforms.items()};avatar=None
        if avatar_enabled:
            pose=copy.deepcopy(panel.pose.__dict__);transform['orbit']=self.transforms['orbit'].value()
            try:
                avatar=avatar_image
                if avatar is not None and avatar.isNull():avatar=None
                if avatar is not None:
                    painter=QPainter(game_program);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    side=round(width*transform['size']);rect=QRect(round(width*transform['x']-side/2),round(height*transform['y']-side/2),side,side)
                    painter.drawImage(rect,avatar);painter.end()
            except Exception:self.input_failed('ラフトV体を描画できません。3Dモデルの読み込み状態を確認してください。');return
        if self.media_enabled.isChecked() and not self.media_image.isNull():self.paint_media_overlay(game_program,width,height)
        now=time.monotonic()
        transition=self.scene_transition
        if transition:
            progress=max(0.,min(1.,(now-transition['started'])/.68))
            source=self.scene_frame(transition['from'],game_program,avatar,now,width,height)
            target=self.scene_frame(transition['to'],game_program,avatar,now,width,height)
            program=self.transition_frame(source,target,progress,now,width,height)
            if progress>=1.:
                self.current_scene=transition['to'];self.scene_transition=None
                if self.current_scene in ('intro','break'):self.scene_started=now
                self.scene_badge.setText({'intro':'OPENING','game':'LIVE','break':'BRB'}[self.current_scene])
                if self.current_scene=='game':self.status.setText('ゲーム画面に切り替わりました。マイクをオンにしました。')
                self.update_mix()
        else:
            program=self.scene_frame(self.current_scene,game_program,avatar,now,width,height)
        self.paint_chat_overlay(program)
        self.engine.submit_frame(program,screen,pose,transform);self.tick_count+=1;self.fps_frames+=1
        elapsed=time.monotonic()-self.fps_epoch
        if elapsed>=1.:
            self.measured_fps=round(self.fps_frames/elapsed);self.fps_frames=0;self.fps_epoch=time.monotonic()
            color='#79e0be' if self.measured_fps>=55 else '#ffd166' if self.measured_fps>=45 else '#ff7b86'
            self.fps_badge.setText(f'● {self.measured_fps} FPS');self.fps_badge.setStyleSheet(f'color:{color};background:#102d2c;padding:5px 10px;border:1px solid #236155;border-radius:10px;font-weight:700')
        if self.tick_count%2==0 and self.isVisible():
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

    def scene_frame(self,name,game_program,avatar,now,width,height):
        if name=='game':return game_program
        frame=QImage(width,height,QImage.Format.Format_RGB32);frame.fill(QColor('#13232c'))
        painter=QPainter(frame)
        if not self.background_frame.isNull():painter.drawImage(0,0,self.background_frame)
        if name=='intro':
            # Use the supplied Minecraft GIF as a soft full-screen scene and
            # keep its pixel art crisp in the foreground, like a game title screen.
            if not self.opening_frame.isNull():
                backdrop=self.opening_frame.scaled(QSize(width,height),Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation)
                source=QRect((backdrop.width()-width)//2,(backdrop.height()-height)//2,width,height)
                painter.drawImage(frame.rect(),backdrop,source)
            painter.fillRect(frame.rect(),QColor(7,13,16,145));painter.fillRect(frame.rect(),self.stream_dot_brush)
            scale=max(1,min(3,height//280));side=200*scale
            art=QRect(round(width*.13),round(height*.50-side/2),side,side)
            painter.fillRect(art.adjusted(-10,-10,10,10),QColor('#f2f1d5'))
            painter.setPen(QPen(QColor('#b4db73'),3,Qt.PenStyle.DotLine));painter.drawRect(art.adjusted(-10,-10,10,10))
            if not self.opening_frame.isNull():
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,False)
                painter.drawImage(art,self.opening_frame.scaled(side,side,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.FastTransformation))
            tx=art.right()+round(width*.045);tw=max(180,width-tx-round(width*.12))
            painter.setPen(QColor('#d5f393'))
            painter.drawText(QRect(tx,round(height*.29),tw,36),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'RAFT  /  LIVE STREAM')
            painter.setPen(QColor('#fffde8'))
            painter.drawText(QRect(tx,round(height*.37),tw,74),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'配信準備中')
            painter.setPen(QPen(QColor('#d1ee83'),max(4,round(width/450)),Qt.PenStyle.DotLine))
            painter.drawLine(tx,round(height*.49),tx+round(width*.09),round(height*.49))
            painter.setPen(QColor('#fffde8'))
            painter.drawText(QRect(tx,round(height*.53),tw,64),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'ゲームの開始を待っています')
            painter.setPen(QColor('#d6e8bd'))
            hint='シーンを切り替えると配信が始まります'
            if self.chat_overlay_enabled.isChecked():
                chat_width=round(width*self.chat_transforms['width'].value()/100)
                chat_left=round(width*self.chat_transforms['x'].value()/100)-chat_width//2
                tw=max(180,min(tw,chat_left-tx-36))
            painter.drawText(QRect(tx,round(height*.62),tw,52),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,hint)
            bar_y=round(height*.75);cell=max(12,round(width*.012));gap=max(5,round(width*.004));count=8;lit=int(now*2.5)%count
            for index in range(count):
                color=QColor('#d1ef84') if index==lit else QColor('#78956a')
                painter.fillRect(QRect(tx+index*(cell+gap),bar_y,cell,10),color)
            painter.setPen(QColor('#e6f0cf'))
            painter.drawText(QRect(tx,bar_y+22,tw,30),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'ALL AUDIO OFF')
            painter.end();return frame
        if avatar is not None and not avatar.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            side=round(height*.56);painter.drawImage(QRect(round(width*.70-side/2),round(height*.48-side/2),side,side),avatar)
        if name=='break':
            age=max(0.,now-getattr(self,'scene_started',now));panel=QRect(round(width*.08),round(height*.29),round(width*.50),round(height*.40))
            painter.setPen(QPen(QColor('#38bd91'),3,Qt.PenStyle.DotLine));painter.setBrush(QColor(245,249,221,245));painter.drawRect(panel)
            painter.fillRect(panel,self.stream_dot_brush)
            painter.setPen(QColor('#257e5e'));painter.drawText(QRect(panel.x()+30,panel.y()+25,panel.width()-60,30),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'PAUSE  /  BRB')
            painter.setPen(QColor('#163b43'));painter.drawText(QRect(panel.x()+30,panel.y()+75,panel.width()-60,60),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'離席中')
            painter.setPen(QColor('#315b53'));painter.drawText(QRect(panel.x()+30,panel.y()+145,panel.width()-60,38),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'まもなく配信に戻ります')
            painter.setPen(QColor('#247d5f'));painter.drawText(QRect(panel.x()+30,panel.y()+205,panel.width()-60,28),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,f'MIC MUTED  •  {int(age)//60:02}:{int(age)%60:02}')
        painter.setPen(QColor(205,245,239,135));painter.drawText(QRect(30,height-58,width-60,28),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'TANUTSUNA STUDIO     /     LIVE SESSION')
        painter.end();return frame

    def transition_frame(self,source,target,progress,now,width,height):
        eased=progress*progress*(3-2*progress);edge=round(-width*.10+width*1.20*eased)
        points=[QPointF(-width,0),QPointF(edge-width*.035,0)]
        bands=24
        for i in range(bands+1):
            y=height*i/bands;tooth=(width*.018 if i%2 else -width*.018)
            points.append(QPointF(edge+tooth,y))
        points.extend([QPointF(edge-width*.035,height),QPointF(-width,height)])
        path=QPainterPath();path.moveTo(points[0])
        for point in points[1:]:path.lineTo(point)
        path.closeSubpath();frame=source.copy();painter=QPainter(frame);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipPath(path);painter.drawImage(0,0,target);painter.setClipping(False)
        glow_x=round(edge+math.sin(now*12)*4);painter.setPen(QPen(QColor('#dbf887'),max(4,round(width/350)),Qt.PenStyle.DotLine));painter.drawLine(glow_x,0,glow_x,height)
        badge=QRect(round(width*.04),round(height*.055),round(width*.25),46)
        painter.setPen(QPen(QColor('#2da97b'),2,Qt.PenStyle.DotLine));painter.setBrush(QColor(244,250,218,245));painter.drawRect(badge)
        painter.setPen(QColor('#183d3c'));painter.drawText(badge.adjusted(10,0,-8,0),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'SCENE SHIFT   /   '+f'{int(progress*100):02}%')
        painter.end();return frame

    def update_mix(self,*_):
        scene_mute=self.current_scene!='game' or self.scene_transition is not None
        for role in ROLES:
            muted=self.mutes[role].isChecked() or scene_mute
            self.engine.set_mix(role,self.gains[role].value()/100,muted)
        if hasattr(self,'voice_gate_indicator'):
            state='シーン演出中のため自動ミュート' if scene_mute else '手動ミュート中' if self.mutes['voice'].isChecked() else 'マイク ON'
            self.voice_gate_indicator.setText(state)

    def show_levels(self,levels):
        for role,value in levels.items():self.meters[role].setValue(min(100,round(value*100)))

    def add_bgm(self):
        files,_=QFileDialog.getOpenFileNames(self,'BGM候補を選択','','MP3 (*.mp3)')
        known={self.bgm.itemData(i) for i in range(self.bgm.count())}
        for file in files:
            if file not in known:self.bgm.addItem(Path(file).name,file)
        if files:self.schedule_preferences_save()

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
        for widget in (self.quality,self.avatar,self.avatar_model,self.mics,self.camera_choice):widget.setEnabled(not locked and not self.previewing)
        for widget in (self.record,self.server,self.key,self.remember):widget.setEnabled(not locked)

    def stop_preview(self):
        self.preparing=False;self.pending_start=False;self.previewing=False;self.stop_capture();self.game=None
        if self.audio:self.audio.stop();self.audio=None
        if self.renderer and not self.isVisible():self.renderer.close();self.renderer=None;self.cached_avatar=None
        if self.camera_owned and self.desk._vtuber:self.desk._vtuber.stop_tracking()
        self.camera_owned=False;self.mutes['voice'].setEnabled(True)
        for source in self.engine.files.values():source.close()
        if self.isVisible():self.timer.start()
        else:self.timer.stop()
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
        self.preferences_timer.stop();self.save_preferences();self.stop_preview();self.timer.stop();self.monitor.stop();self.engine.shutdown();self.jobs.shutdown()
        if self.renderer:self.renderer.close();self.renderer=None
