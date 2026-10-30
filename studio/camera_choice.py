"""A shared, remembered camera choice for recording, live and avatar settings."""
import weakref
import re
from PySide6.QtCore import QSettings,Signal
from PySide6.QtWidgets import QComboBox
from PySide6.QtMultimedia import QMediaDevices


def camera_key(label):
    return re.sub(r'[\W_]','',label.lower().replace('カメラ','camera').replace('の','').replace('(built-in)','').replace('（内蔵）',''))


class CameraChoice(QComboBox):
    selectedCameraChanged=Signal(str)
    instances=weakref.WeakSet()
    def __init__(self,parent=None):
        super().__init__(parent);self.instances.add(self);self.devices=QMediaDevices(self)
        self.refresh();self.currentIndexChanged.connect(self.selected)
        self.devices.videoInputsChanged.connect(self.refresh)
    def refresh(self):
        preferred=QSettings('TanutsunaStudio','Avatar').value('cameraLabel','')
        self.blockSignals(True);self.clear();self.addItem('標準のカメラ','')
        for device in QMediaDevices.videoInputs():self.addItem(device.description(),device.description())
        index=self.findData(preferred)
        if preferred and index<0:index=next((i for i in range(1,self.count()) if camera_key(self.itemData(i))==camera_key(preferred)),-1)
        if preferred and index<0:self.addItem(preferred+'（接続を確認）',preferred);index=self.count()-1
        self.setCurrentIndex(max(0,index));self.blockSignals(False)
    def selected(self,*_):
        QSettings('TanutsunaStudio','Avatar').setValue('cameraLabel',self.currentData() or '')
        for selector in list(self.instances):
            if selector is not self:selector.refresh()
            selector.selectedCameraChanged.emit(self.currentData() or '')
    @staticmethod
    def remember(label):
        QSettings('TanutsunaStudio','Avatar').setValue('cameraLabel',label)
        for selector in list(CameraChoice.instances):selector.refresh()
