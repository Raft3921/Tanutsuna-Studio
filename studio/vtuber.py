from pathlib import Path
import json
import math
import shutil
import time
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QUrl, QSettings, QCameraPermission
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QApplication,QWidget,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QGroupBox,
    QLabel,QPushButton,QComboBox,QSlider,QCheckBox,QFileDialog,QMessageBox,QSplitter,QDoubleSpinBox,QScrollArea)
from .avatar_view import AvatarViewport
from .tracking import Pose, TrackerServer, smooth_pose
from .mouth import VOWELS, LABELS, DEFAULT_SHAPES, dimensions


class AvatarOutput(QDialog):
    def __init__(self,parent,software=False):
        super().__init__(parent,Qt.WindowType.Window)
        self.setWindowTitle('V体出力 — たぬつな')
        self.resize(720,720)
        self.view=AvatarViewport(self,software=software)
        self.view.background=QColor('#00ff00')
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.addWidget(self.view)


class VtuberPanel(QWidget):
    def __init__(self,parent=None,project_getter=None):
        super().__init__(parent)
        self.project_getter=project_getter
        self.server=TrackerServer()
        try:self.server.state.voice_gain=min(5,max(.5,float(QSettings('TanutsunaStudio','Avatar').value('voiceMouthGain',2.5))))
        except (TypeError,ValueError):pass
        self.mouth_shapes=dict(DEFAULT_SHAPES)
        try:
            stored=json.loads(QSettings('TanutsunaStudio','Avatar').value('mouthShapes','{}'))
            for vowel in VOWELS:
                width,height=stored.get(vowel,DEFAULT_SHAPES[vowel])
                if math.isfinite(width) and math.isfinite(height):
                    self.mouth_shapes[vowel]=(max(.55,min(1.9,width)),max(.15,min(1.3,height)))
        except (ValueError,TypeError):pass
        self.pose=Pose();self.neutral=[0,0,0];self.last=time.monotonic();self.epoch=self.last
        self._last_camera_label='';self._last_microphone_label='';self._camera_permission_pending=False;self.recording=None;self.record_path=None;self.record_start=0;self.output=None;self.camera_window=None
        self.setMinimumHeight(420)
        outer=QVBoxLayout(self)
        controls=QHBoxLayout()
        self.mode=QComboBox();self.mode.addItems(['手動で可動確認','可動テスト（自動）','カメラ追跡'])
        from .camera_choice import CameraChoice
        self.camera_choice=CameraChoice(self);self.camera_choice.selectedCameraChanged.connect(self.change_camera);controls.addWidget(QLabel('カメラ'));controls.addWidget(self.camera_choice)
        controls.addWidget(QLabel('入力'));controls.addWidget(self.mode)
        self.start_button=QPushButton('カメラ追跡を開始');self.start_button.clicked.connect(self.start_tracking)
        controls.addWidget(self.start_button)
        stop=QPushButton('追跡停止');stop.clicked.connect(self.stop_tracking);controls.addWidget(stop)
        controls.addStretch()
        outer.addLayout(controls)
        split=QSplitter()
        self.viewport=AvatarViewport(self)
        split.addWidget(self.viewport)
        settings=QWidget();right=QVBoxLayout(settings)
        group=QGroupBox('手動の可動確認');form=QFormLayout(group)
        self.sliders={}
        for key,title,low,high,value in [('yaw','左右',-65,65,0),('pitch','上下',-40,40,0),('roll','傾き',-40,40,0),
            ('jawOpen','口を開く',0,100,60)]:
            slider=QSlider(Qt.Orientation.Horizontal);slider.setRange(low,high);slider.setValue(value)
            self.sliders[key]=slider;form.addRow(title,slider)
        right.addWidget(group)
        group=QGroupBox('あいうえおの口形');form=QFormLayout(group)
        self.vowel=QComboBox();self.vowel.addItems(LABELS)
        self.vowel_width=QDoubleSpinBox();self.vowel_width.setRange(.55,1.9);self.vowel_width.setSingleStep(.05)
        self.vowel_height=QDoubleSpinBox();self.vowel_height.setRange(.15,1.3);self.vowel_height.setSingleStep(.05)
        self.stroke=QDoubleSpinBox();self.stroke.setRange(.8,1.8);self.stroke.setSingleStep(.05)
        try:self.stroke.setValue(float(QSettings('TanutsunaStudio','Avatar').value('mouthStroke',1.15)))
        except (ValueError,TypeError):self.stroke.setValue(1.15)
        form.addRow('口形を選ぶ',self.vowel);form.addRow('横幅',self.vowel_width);form.addRow('開いた時の高さ',self.vowel_height);form.addRow('口の線の太さ',self.stroke)
        self.vowel.currentIndexChanged.connect(self.load_mouth_shape)
        self.vowel_width.valueChanged.connect(self.save_mouth_shape);self.vowel_height.valueChanged.connect(self.save_mouth_shape)
        self.stroke.valueChanged.connect(self.save_mouth_shape)
        self.load_mouth_shape()
        restore=QPushButton('口形を標準に戻す');restore.clicked.connect(self.reset_mouth_shapes);form.addRow(restore)
        note=QLabel('カメラ追跡では唇の形に加え、マイク音量から普通の会話中の小さな口の動きも補います。音声はこのPC内だけで解析します。')
        note.setWordWrap(True);form.addRow(note)
        right.addWidget(group)
        group=QGroupBox('カメラ追跡の動き');form=QFormLayout(group)
        self.motion_gain=QDoubleSpinBox();self.motion_gain.setRange(.5,2.5);self.motion_gain.setSingleStep(.1);self.motion_gain.setValue(1.7)
        self.position_gain=QDoubleSpinBox();self.position_gain.setRange(0,2.5);self.position_gain.setSingleStep(.1);self.position_gain.setValue(1.3)
        self.mouth_gain=QDoubleSpinBox();self.mouth_gain.setRange(.5,4);self.mouth_gain.setSingleStep(.1);self.mouth_gain.setValue(2.0)
        form.addRow('向きの倍率',self.motion_gain);form.addRow('移動の倍率',self.position_gain);form.addRow('口の開きの倍率',self.mouth_gain)
        right.addWidget(group)
        row=QHBoxLayout();reset=QPushButton('初期姿勢');reset.clicked.connect(self.reset_pose)
        neutral=QPushButton('今の向きを正面に');neutral.clicked.connect(self.calibrate)
        row.addWidget(reset);row.addWidget(neutral);right.addLayout(row)
        camera=QGroupBox('表示と出力');form=QFormLayout(camera)
        self.orbit=QSlider(Qt.Orientation.Horizontal);self.orbit.setRange(-180,180)
        form.addRow('モデルを回して確認',self.orbit)
        self.background=QComboBox();self.background.addItems(['グレー','緑（別素材用）','青（確認用）'])
        form.addRow('出力の背景',self.background)
        self.compatibility=QCheckBox('互換表示（3Dが表示されない場合）')
        self.compatibility.setChecked(self.viewport.software)
        self.compatibility.toggled.connect(self.switch_renderer)
        form.addRow(self.compatibility)
        output=QPushButton('たぬきだけの出力画面を開く');output.clicked.connect(self.open_output)
        form.addRow(output)
        right.addWidget(camera)
        self.record_button=QPushButton('動きを記録…');self.record_button.clicked.connect(self.toggle_recording)
        right.addWidget(self.record_button)
        model_button=QPushButton('3Dモデル（GLB）を保存…');model_button.clicked.connect(self.export_model)
        right.addWidget(model_button)
        right.addStretch();scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(settings);split.addWidget(scroll);split.setSizes([500,340])
        outer.addWidget(split,1)
        self.status=QLabel('手動で3Dモデルの可動部を確認できます。カメラは停止中です。')
        self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self.status)
        help_=QLabel('カメラ追跡：ソフト内のカメラ画面で「カメラを開始」。顔の向き・移動と口の開閉を反映します。サングラスは固定。耳の揺れは補助アニメーションです。')
        help_.setWordWrap(True);outer.addWidget(help_)
        self.render_status=QLabel('3D描画：互換表示' if self.viewport.software else '3D描画：準備中')
        self.render_status.setWordWrap(True);outer.addWidget(self.render_status)
        self.viewport.backendChanged.connect(self.renderer_changed)
        self.timer=QTimer(self);self.timer.setInterval(120 if self.viewport.software else 33);self.timer.timeout.connect(self.tick);self.timer.start()

    def load_mouth_shape(self,*_):
        width,height=self.mouth_shapes[VOWELS[self.vowel.currentIndex()]]
        for widget,value in ((self.vowel_width,width),(self.vowel_height,height)):
            widget.blockSignals(True);widget.setValue(value);widget.blockSignals(False)

    def save_mouth_shape(self,*_):
        self.mouth_shapes[VOWELS[self.vowel.currentIndex()]]=(self.vowel_width.value(),self.vowel_height.value())
        settings=QSettings('TanutsunaStudio','Avatar')
        settings.setValue('mouthShapes',json.dumps(self.mouth_shapes));settings.setValue('mouthStroke',self.stroke.value())

    def reset_mouth_shapes(self):
        self.mouth_shapes=dict(DEFAULT_SHAPES);self.load_mouth_shape();self.stroke.setValue(1.15);self.save_mouth_shape()

    def renderer_changed(self,software,message):
        self.compatibility.blockSignals(True);self.compatibility.setChecked(software);self.compatibility.blockSignals(False)
        self.render_status.setText('3D描画：'+message)
        if hasattr(self,'timer'):self.timer.setInterval(120 if software else 33)

    def start_tracking(self,checked=False,automatic=False):
        try:
            app=QApplication.instance();permission=QCameraPermission();permission_status=app.checkPermission(permission)
            if permission_status==Qt.PermissionStatus.Undetermined:
                if not self._camera_permission_pending:
                    self._camera_permission_pending=True
                    self.status.setText('OSのカメラ許可を確認しています…')
                    app.requestPermission(permission,self,lambda _result:self._camera_permission_finished(automatic))
                return True
            if permission_status==Qt.PermissionStatus.Denied:
                message='開始できません：OSのカメラ許可がありません。システム設定 ＞ プライバシーとセキュリティ ＞ カメラで「たぬつな Studio」を許可し、アプリを再起動してください。'
                self.server.state.update({'detected':False,'pose':{},'status':message})
                self.status.setText(message)
                if not automatic:QMessageBox.warning(self,'カメラの許可が必要です',message.removeprefix('開始できません：'))
                return False
            assets=Path(__file__).parent/'assets/tracker'
            if not (assets/'face_landmarker.task').is_file():raise ValueError('追跡モデルがありません。配布ZIPをすべて展開してください。')
            self.server.state.camera_label='';self.server.state.microphone_label='';self._last_camera_label='';self._last_microphone_label=''
            url=self.server.start()
            self.mode.setCurrentIndex(2)
            self.neutral=[0,0,0]
            if self.camera_window is None:
                from .camera_window import CameraWindow
                self.camera_window=CameraWindow(self)
                self.camera_window.stopped.connect(self.stop_tracking)
            self.camera_window.open_tracker(url,automatic=automatic)
            self.status.setText('カメラを自動で準備中。初回のOS許可を確認してください。' if automatic else 'ソフト内のカメラ画面で「カメラを開始」を押してください。')
            self.start_button.setText('カメラ画面を開く')
            return True
        except Exception as exc:
            if automatic:raise
            QMessageBox.warning(self,'追跡を開始できません',str(exc));return False

    def _camera_permission_finished(self,automatic):
        self._camera_permission_pending=False
        self.start_tracking(automatic=automatic)

    def change_camera(self,label):
        if self.server.server is None:return
        visible=bool(self.camera_window and self.camera_window.isVisible())
        automatic=bool(self.server.state.camera_label) or not visible
        self.stop_tracking()
        def restart():
            self.start_tracking(automatic=automatic)
            if visible and self.camera_window:self.camera_window.show()
        QTimer.singleShot(0,restart)

    def stop_tracking(self):
        if self.camera_window:self.camera_window.stop()
        self.server.stop();self.server.state.detected=False
        self.mode.setCurrentIndex(0);self.start_button.setText('カメラ追跡を開始')
        self.status.setText('カメラ入力と追跡を停止しました。')

    def calibrate(self):
        raw,detected,_,_=self.server.state.snapshot()
        if self.mode.currentIndex()==2 and detected:
            self.neutral=[raw.yaw,raw.pitch,raw.roll]
        else:self.reset_pose()

    def reset_pose(self):
        for key,slider in self.sliders.items():slider.setValue(60 if key=='jawOpen' else 0)
        self.neutral=[0,0,0];self.orbit.setValue(0)

    def switch_renderer(self,software):
        self.viewport.set_software(software)
        if self.output:self.output.view.set_software(software)

    def open_output(self):
        if self.output is None:self.output=AvatarOutput(self,self.compatibility.isChecked())
        self.output.show();self.output.raise_();self.output.activateWindow()

    def tick(self):
        if not self.isVisible() and not (self.output and self.output.isVisible()) and self.server.server is None and self.recording is None and not getattr(self,'synced_recording',False):return
        label=self.server.state.camera_label
        if label and label!=self._last_camera_label:
            self._last_camera_label=label
            from .camera_choice import CameraChoice
            CameraChoice.remember(label)
        microphone_label=self.server.state.microphone_label
        if microphone_label and microphone_label!=self._last_microphone_label:
            self._last_microphone_label=microphone_label
            QSettings('TanutsunaStudio','Avatar').setValue('microphoneLabel',microphone_label)
        voice_gain=self.server.state.voice_gain
        try:stored_voice_gain=float(QSettings('TanutsunaStudio','Avatar').value('voiceMouthGain',2.5))
        except (TypeError,ValueError):stored_voice_gain=2.5
        if abs(stored_voice_gain-voice_gain)>.04:
            QSettings('TanutsunaStudio','Avatar').setValue('voiceMouthGain',voice_gain)
        now=time.monotonic();dt=now-self.last;self.last=now;t=now-self.epoch
        mode=self.mode.currentIndex();detected=False
        if mode==2:
            raw,detected,message,last_seen=self.server.state.snapshot()
            gain=self.motion_gain.value();move=self.position_gain.value()
            target=Pose(**{**raw.__dict__,'yaw':max(-65,min(65,(raw.yaw-self.neutral[0])*gain)),
                'pitch':max(-40,min(40,(raw.pitch-self.neutral[1])*gain)),'roll':max(-40,min(40,(raw.roll-self.neutral[2])*gain)),
                'headX':max(-.65,min(.65,raw.headX*move)),'headY':max(-.45,min(.45,raw.headY*move)),
                'headZ':max(-.55,min(.55,raw.headZ*move)), 'jawOpen':min(1,max(0,raw.jawOpen)*self.mouth_gain.value())}) if detected else Pose(jawOpen=0)
            self.status.setText(('カメラ追跡中' if detected else '追跡信号なし・待機姿勢')+f'  左右 {self.pose.yaw:.0f}° / 上下 {self.pose.pitch:.0f}° / 傾き {self.pose.roll:.0f}°')
        elif mode==1:
            target=Pose(yaw=math.sin(t*.8)*32,pitch=math.sin(t*1.1)*16,roll=math.sin(t*.6)*12,
                jawOpen=(math.sin(t*3)+1)*.45,headX=math.sin(t*.9)*.16,headY=math.sin(t*1.3)*.07)
            self.status.setText('可動テスト（自動アニメーション）。カメラ追跡の結果ではありません。')
        else:
            target=Pose(**{k:s.value()/(1 if k in ('yaw','pitch','roll') else 100) for k,s in self.sliders.items()})
            self.status.setText('手動の可動確認。モデルを回して背面や横顔も確認できます。')
        if mode != 2:
            vowel=VOWELS[int(t/1.3)%5] if mode == 1 else VOWELS[self.vowel.currentIndex()]
            setattr(target,'mouth'+vowel,1)
        target.mouthWidth,target.mouthHeight=dimensions(target,self.mouth_shapes)
        target.mouthStroke=self.stroke.value()
        self.pose=smooth_pose(self.pose,target,dt)
        self.viewport.orbit=self.orbit.value()
        if self.viewport.isVisible():self.viewport.update_pose(self.pose,t)
        if self.output and self.output.isVisible():
            self.output.view.background=QColor(['#4b535c','#00ff00','#0000ff'][self.background.currentIndex()])
            self.output.view.update_pose(self.pose,t)
        if self.recording:
            frame={'time':round(now-self.record_start,4),'source':['manual','demo','camera'][mode],'detected':detected,'pose':self.pose.__dict__}
            self.recording.write(json.dumps(frame,separators=(',',':'))+'\n')
            if int((now-self.record_start)*30)%30==0:self.recording.flush()

    def toggle_recording(self):
        if self.recording:
            self.recording.close();self.recording=None;self.record_button.setText('動きを記録…')
            self.status.setText('動きの数値を保存しました：'+str(self.record_path));return
        start=Path.home()
        try:
            project=self.project_getter() if self.project_getter else None
            if project:start=Path(project['folder'])/'02-assets'
        except ValueError:pass
        file,_=QFileDialog.getSaveFileName(self,'動きの記録（映像ではありません）',str(start/('tanuki-motion-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.jsonl')),'動きの記録 (*.jsonl)')
        if not file:return
        try:
            self.recording=open(file,'w',encoding='utf-8');self.record_path=Path(file);self.record_start=time.monotonic()
            self.recording.write(json.dumps({'format':'tanutsuna-motion-v1','model':'tanuki-v0.3','faceOnly':True,'fixedEyes':True})+'\n')
            self.record_button.setText('記録を停止して保存')
        except Exception as exc:QMessageBox.warning(self,'記録できません',str(exc))

    def export_model(self):
        file,_=QFileDialog.getSaveFileName(self,'3Dモデルを保存','tanutsuna-tanuki.glb','glTFモデル (*.glb)')
        if file:
            try:shutil.copy2(Path(__file__).parent/'assets/tanuki/tanuki.glb',file)
            except Exception as exc:QMessageBox.warning(self,'モデルを保存できません',str(exc))

    def shutdown(self):
        if self.camera_window:self.camera_window.stop()
        self.timer.stop();self.server.stop()
        if self.recording:self.recording.close();self.recording=None
        if self.output:self.output.view.release();self.output.close()
        self.viewport.release()
