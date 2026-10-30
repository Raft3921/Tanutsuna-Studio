"""Packaged-app verification with isolated project data and real framebuffer evidence."""
from pathlib import Path
import json
import urllib.request
import os,time
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage


def schedule_editor_check(app,window,root):
    root=Path(root)
    def verify():
        try:
            project=window.workspace.available_projects()[0]
            window.desk.select(project,remember=False)
            window.show_editor()
            editor=window.loaded_editor
            editor.library.validate()
            report={'page':window.desk.current_page,'clips':len(editor.sequence.clips),
                'assets':len(editor.library.assets),'tracks':sorted({c.track for c in editor.sequence.clips}),
                'pathExists':Path(editor.path).is_file()}
            assert report['page']=='edit' and report['clips']>0 and report['pathExists'],report
            (root/'editor-ok.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            window.grab().save(str(root/'editor-ui.png'))
        except Exception:
            import traceback
            (root/'editor-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        finally:window.close();app.quit()
    QTimer.singleShot(750,verify)


def schedule_vtuber_check(app,window,root):
    root=Path(root)
    if window.desk.current is None:
        window.workspace.data['root']=str(root/'videos')
        project=window.workspace.create('描画確認')
        window.desk.select(project)
    window.desk.navigate('vtuber')
    def verify():
        view=window.desk.vtuber.viewport;backend=view.backend
        image=backend.grabFramebuffer() if hasattr(backend,'grabFramebuffer') else backend.grab().toImage()
        image=image.convertToFormat(QImage.Format.Format_RGBA8888)
        pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine()//4,4)[:,:image.width(),:3]
        bg=np.array([view.background.red(),view.background.green(),view.background.blue()])
        count=int(np.count_nonzero(np.max(np.abs(pixels.astype(int)-bg),axis=2)>12))
        server=window.desk.vtuber.server
        origin=server.start().split('/#')[0]
        camera_assets={}
        for route in ('/', '/camera/camera.js','/camera/camera.css','/tracker/vision_bundle.mjs','/tracker/face_landmarker.task'):
            with urllib.request.urlopen(origin+route,timeout=5) as response:
                camera_assets[route]={'status':response.status,'bytes':len(response.read())}
        report={'backend':type(backend).__name__,'layoutChildren':view.layout_.count(),'width':image.width(),'height':image.height(),'modelPixels':count,'error':view.last_render_error,'cameraAssets':camera_assets}
        (root/'vtuber-renderer.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        image.save(str(root/'vtuber-frame.png'));window.grab().save(str(root/'vtuber-ui.png'))
        if count>64 and view.layout_.count()==1:(root/'startup-ok.txt').write_text('Vtuber page rendered',encoding='utf-8')
        if os.environ.get('TANUTSUNA_CAMERA_CHECK')=='1':
            check_camera()
        else:window.close();app.quit()
    def check_camera():
        panel=window.desk.vtuber
        panel.start_tracking(automatic=True)
        started=time.monotonic();phase=['load']
        def poll():
            if time.monotonic()-started>45:
                (root/'camera-error.txt').write_text(str(panel.server.state.snapshot()[2]),encoding='utf-8')
                window.close();app.quit();return
            if panel.server.state.snapshot()[1]:
                (root/'camera-ok.txt').write_text('External browser camera tracking connected',encoding='utf-8')
                panel.stop_tracking()
                window.close();app.quit();return
            QTimer.singleShot(250,poll)
        QTimer.singleShot(500,poll)
    QTimer.singleShot(2600,verify)
