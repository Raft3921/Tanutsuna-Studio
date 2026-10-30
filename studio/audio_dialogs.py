"""Discoverable, review-before-apply automation inside the native editor."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QFormLayout,QDoubleSpinBox,QComboBox,QTableWidget,QTableWidgetItem,QHeaderView,
    QTabWidget,QWidget,QSpinBox,QFileDialog)
from .jobs import Jobs
from .audio_tools import ROLES,role_paths,audio_role,analyze_silence,cut_library,apply_mix


def note(text):
    label=QLabel(text);label.setWordWrap(True);return label


class SilenceDialog(QDialog):
    def __init__(self,editor):
        super().__init__(editor);self.editor=editor;self.ranges=[];self.revision=None
        self.setWindowTitle('無音カット — 映像と音を同期して短縮');self.resize(640,480)
        self.jobs=Jobs(self);self.jobs.finished.connect(self.finished_analysis)
        layout=QVBoxLayout(self)
        layout.addWidget(note('声の休止区間を調べ、画面・PC音・マイク・V体・BGMを同じ時間でカットします。後ろの素材も一緒に前へ詰めます。元ファイルは保持し、編集の「元に戻す」で取り消せます。'))
        layout.addWidget(note('解析対象は現在開いている編集版です。複合クリップは中を開いて実行します。同じ複合クリップを使う親の編集版も同期して短縮します。音量変更・ミュート前の元音声を解析します。'))
        form=QFormLayout();self.reference=QComboBox();paths=role_paths(editor.shell.desk.selected_project())
        self.preset=QComboBox()
        self.preset.addItem('おすすめ（会話を残してカット）',(-38,.7,.12))
        self.preset.addItem('ゆったり（短い間は残す）',(-45,1.0,.20))
        self.preset.addItem('テンポ重視（間を多めにカット）',(-32,.45,.08))
        form.addRow('カットの強さ',self.preset)
        self.voices=[]
        for clip in editor.sequence.ordered():
            asset=editor.library.assets[clip.asset_id]
            if asset.kind=='media' and asset.has_audio:
                if audio_role(asset,paths)=='voice':self.voices.append(clip.id)
        if self.voices:self.reference.addItem(f'すべての自分の声（{len(self.voices)}クリップ）',self.voices)
        for clip in editor.sequence.ordered():
            asset=editor.library.assets[clip.asset_id]
            if asset.kind=='media' and asset.has_audio:
                self.reference.addItem(f'{asset.name} ／ {clip.start/float(editor.sequence.rate):.1f}秒〜',[clip.id])
        form.addRow('解析する声',self.reference)
        self.threshold=QDoubleSpinBox();self.threshold.setRange(-70,-10);self.threshold.setValue(-38);self.threshold.setSuffix(' dB')
        self.minimum=QDoubleSpinBox();self.minimum.setRange(.2,10);self.minimum.setValue(.7);self.minimum.setSuffix(' 秒')
        self.padding=QDoubleSpinBox();self.padding.setRange(0,1);self.padding.setValue(.12);self.padding.setSuffix(' 秒')
        self.preset.currentIndexChanged.connect(self.apply_preset)
        for title,field in [('無音とみなす音量以下',self.threshold),('無音の最小の長さ',self.minimum),('言葉の前後に残す余白',self.padding)]:form.addRow(title,field)
        layout.addLayout(form)
        self.status=note('解析後に短縮時間を確認してから適用できます。');layout.addWidget(self.status);layout.addStretch()
        row=QHBoxLayout();self.analyze=QPushButton('無音を解析');self.analyze.setObjectName('Primary');self.analyze.clicked.connect(self.start_analysis)
        self.apply=QPushButton('同期カットを適用');self.apply.setEnabled(False);self.apply.clicked.connect(self.apply_result)
        self.cancel=QPushButton('閉じる');self.cancel.clicked.connect(self.reject)
        for button in (self.analyze,self.apply,self.cancel):row.addWidget(button)
        layout.addLayout(row)
        for field in (self.reference,self.threshold,self.minimum,self.padding):
            signal=field.currentIndexChanged if field is self.reference else field.valueChanged
            signal.connect(self.invalidate)

    def apply_preset(self,index):
        values=self.preset.itemData(index)
        if not values:return
        for field,value in zip((self.threshold,self.minimum,self.padding),values):
            field.setValue(value)

    def invalidate(self,*_):
        self.ranges=[];self.apply.setEnabled(False)

    def start_analysis(self):
        if not self.reference.currentData():self.status.setText('解析できる音声がありません。先に声の素材を配置してください。');return
        if self.editor.shell.desk.busy:self.status.setText('収録や素材処理の完了後に実行してください。');return
        self.editor.preview.pause();self.invalidate();self.revision=self.editor.revision
        library=self.editor.library.clone();sid=self.editor.sequence.id;ids=set(self.reference.currentData())
        threshold,minimum,padding=self.threshold.value(),self.minimum.value(),self.padding.value()
        self.jobs.submit('silence',lambda cancel:analyze_silence(library,sid,ids,threshold,minimum,padding,cancel))
        self.analyze.setEnabled(False);self.cancel.setText('中止して閉じる')
        for field in (self.reference,self.threshold,self.minimum,self.padding):field.setEnabled(False)
        self.status.setText('音声を解析中です。解析はバックグラウンドで行います。')

    def finished_analysis(self,tag,result,error):
        self.analyze.setEnabled(True);self.cancel.setText('閉じる')
        for field in (self.reference,self.threshold,self.minimum,self.padding):field.setEnabled(True)
        if error:self.status.setText(error);return
        self.ranges=result
        rate=float(self.editor.sequence.rate);removed=sum(b-a for a,b in result)/rate
        total=self.editor.sequence.end/rate
        self.status.setText(f'{len(result)}区間を検出。{removed:.2f}秒短縮し、全体は約{max(0,total-removed):.2f}秒になります。\n画面・音・V体・追加素材を含む全トラックに同じ区間を適用します。' if result else '指定した条件でカットする無音はありませんでした。')
        self.apply.setEnabled(bool(result))

    def apply_result(self):
        if self.editor.revision!=self.revision:
            self.invalidate();self.status.setText('解析後に編集が変わりました。もう一度解析してください。');return
        if self.editor.commit('無音を全トラック同期でカット',lambda:cut_library(self.editor.library,self.editor.sequence.id,self.ranges)):
            self.editor.save(False);self.editor.fit_timeline();self.accept()

    def done(self,result):
        self.jobs.shutdown();super().done(result)


class MixerDialog(QDialog):
    def __init__(self,editor):
        super().__init__(editor);self.editor=editor;self.setWindowTitle('音の役割・全体音量・BGM');self.resize(800,630)
        layout=QVBoxLayout(self)
        layout.addWidget(note('素材ごとに声・ゲーム音・効果音・BGMを指定します。割合は現在の音量に掛ける値です。100%で維持、50%で半分。フェードとミュートは保持します。'))
        tabs=QTabWidget();layout.addWidget(tabs,1)
        roles=QWidget();rl=QVBoxLayout(roles);self.table=QTableWidget(0,2);self.table.setHorizontalHeaderLabels(['音のある素材','役割'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1,180);self.table.verticalHeader().setDefaultSectionSize(48);self.table.verticalHeader().hide();self.fields={};paths=role_paths(editor.shell.desk.selected_project())
        for asset in editor.library.assets.values():
            if asset.kind!='media' or not asset.has_audio:continue
            row=self.table.rowCount();self.table.insertRow(row)
            item=QTableWidgetItem(asset.name);item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable);self.table.setItem(row,0,item)
            combo=QComboBox()
            for role,title in ROLES.items():
                if role=='bgm' and (Path(asset.path).suffix.lower()!='.mp3' or asset.has_video):continue
                combo.addItem(title,role)
            combo.setCurrentIndex(max(0,combo.findData(audio_role(asset,paths))))
            self.table.setCellWidget(row,1,combo);self.fields[asset.id]=combo
        rl.addWidget(self.table);tabs.addTab(roles,'1  音の役割')
        balance=QWidget();bl=QVBoxLayout(balance);form=QFormLayout();self.factors={}
        for role,title in ROLES.items():
            field=QSpinBox();field.setRange(0,500);field.setValue(100);field.setSuffix(' %');self.factors[role]=field;form.addRow(title,field)
        bl.addLayout(form)
        preset=QPushButton('声を中心にする：声100 / ゲーム40 / 効果音75 / BGM20')
        preset.clicked.connect(lambda:[self.factors[k].setValue(v) for k,v in {'voice':100,'desktop':40,'sfx':75,'bgm':20}.items()]);bl.addWidget(preset)
        bl.addWidget(note('割合は現在の音量に掛かります。100%で維持、200%で2倍、500%で5倍です。大きく上げると音が割れやすくなります。現在の編集版と複合クリップに適用します。'))
        bl.addStretch();tabs.addTab(balance,'2  全体の音量')
        bgm=QWidget();bgl=QVBoxLayout(bgm);bgl.addWidget(note('複数のMP3を候補に追加し、この編集版に使う1曲を選べます。選んだBGMは先頭から専用トラックへ配置します。曲が短い場合は曲の終端までです。'))
        self.bgm=QComboBox();self.bgm.addItem('現在のBGMを維持','keep');self.bgm.addItem('この編集版のBGM配置を外す',None)
        for asset in editor.library.assets.values():
            if asset.kind=='media' and asset.has_audio and not asset.has_video and Path(asset.path).suffix.lower()=='.mp3':self.bgm.addItem(asset.name,asset.id)
        bgl.addWidget(self.bgm)
        add=QPushButton('MP3の候補を複数追加…');add.clicked.connect(self.add_candidates);bgl.addWidget(add)
        bgl.addWidget(note('候補の追加は素材への保存だけを行います。追加後、もう一度この画面を開いて曲を選んでください。BGMの入替は現在の編集版に適用します。複合クリップ内のBGMは、その中を開いて入れ替えられます。'))
        bgl.addStretch();tabs.addTab(bgm,'3  BGMを選ぶ')
        self.status=note('「役割と音量を適用」で保存します。元に戻すこともできます。');layout.addWidget(self.status)
        row=QHBoxLayout();apply=QPushButton('役割と音量を適用');apply.setObjectName('Primary');apply.clicked.connect(self.apply_mix)
        close=QPushButton('閉じる');close.clicked.connect(self.reject);row.addStretch();row.addWidget(apply);row.addWidget(close);layout.addLayout(row)

    def apply_mix(self):
        assignments={key:field.currentData() for key,field in self.fields.items()}
        factors={key:field.value()/100 for key,field in self.factors.items()}
        chosen=self.bgm.currentData()
        if chosen not in ('keep',None):assignments[chosen]='bgm'
        if self.editor.commit('音の役割・全体音量・BGMを調整',lambda:apply_mix(self.editor.library,self.editor.sequence,assignments,factors,chosen)):
            self.editor.save(False);self.accept()
        else:self.status.setText('変更はありません。設定を変えて適用してください。')

    def add_candidates(self):
        files,_=QFileDialog.getOpenFileNames(self,'BGM候補のMP3を選択','','MP3音声 (*.mp3)')
        if not files:return
        try:self.editor.shell.import_files(files,audio_role='bgm',place=False)
        except Exception as exc:self.status.setText(str(exc));return
        self.accept()
