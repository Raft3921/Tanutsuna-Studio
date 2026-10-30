"""Build a self-contained Mac verification app using this Mac's Python runtime."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'studio/version.py').read_text(encoding='utf-8').split('STUDIO_VERSION = "', 1)[1].split('"', 1)[0]

def repair_webengine_layout(bundle):
    # Qt 6.12's split WebEngine wheel is collected under Versions/Resources by
    # PyInstaller; restore the standard framework paths its helper searches.
    import shutil
    framework=bundle/'Contents/Frameworks/PySide6/Qt/lib/QtWebEngineCore.framework'
    misplaced=framework/'Versions/Resources'
    if misplaced.is_dir():
        for entry in misplaced.iterdir():
            destination=framework/'Versions/A'/entry.name
            if entry.is_dir():shutil.copytree(entry,destination,dirs_exist_ok=True,symlinks=True)
            else:shutil.copy2(entry,destination)
        shutil.rmtree(misplaced)
    assert (framework/'Helpers/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess').is_file()
    assert (framework/'Resources/icudtl.dat').is_file()

def build():
    if sys.platform != 'darwin':
        raise SystemExit('Mac上で実行してください。')
    app = ROOT / 'vendor/cutraft-win/app'
    native = ROOT / 'studio/native'
    subprocess.run(['swiftc', '-parse-as-library', '-O', '-target', 'arm64-apple-macos15.0',
        str(native/'ScreenRecorder.swift'), '-o', str(native/'screen-recorder'),
        '-framework', 'ScreenCaptureKit', '-framework', 'AVFoundation', '-framework', 'VideoToolbox',
        '-Xlinker', '-sectcreate', '-Xlinker', '__TEXT', '-Xlinker', '__info_plist', '-Xlinker', str(native/'RecorderInfo.plist')], check=True)
    subprocess.run(['swiftc','-parse-as-library','-O','-target','arm64-apple-macos15.0',str(native/'DesktopAudio.swift'),'-o',str(native/'desktop-audio'),'-framework','ScreenCaptureKit','-framework','AVFoundation','-Xlinker','-sectcreate','-Xlinker','__TEXT','-Xlinker','__info_plist','-Xlinker',str(native/'RecorderInfo.plist')],check=True)
    subprocess.run(['swiftc','-parse-as-library','-O','-target','arm64-apple-macos15.0',str(native/'LiveWindow.swift'),'-o',str(native/'live-window'),'-framework','ScreenCaptureKit','-framework','CoreMedia','-framework','CoreVideo','-framework','AppKit','-Xlinker','-sectcreate','-Xlinker','__TEXT','-Xlinker','__info_plist','-Xlinker',str(native/'RecorderInfo.plist')],check=True)
    if not (ROOT/'build/live-deps/av').is_dir():
        subprocess.run([sys.executable,'-m','pip','install','--target',str(ROOT/'build/live-deps'),'av==17.1.0'],check=True)
    if not (app / 'cutraft/app.py').is_file():
        raise SystemExit('先に scripts/build_native.py --prepare-only を実行してください。')
    # Reapply the maintained fixes without copying the Windows runtime.
    import shutil
    for source in (ROOT / 'editor_overrides').glob('*.py'):
        shutil.copy2(source, app / 'cutraft' / source.name)
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
        '--name', 'Tanutsuna Studio', '--osx-bundle-identifier', 'com.tanutsuna.studio',
        '--paths', str(app), '--paths',str(ROOT/'build/live-deps'),'--collect-all','av','--collect-all', 'imageio_ffmpeg',
        '--icon', str(app / 'cutraft/resources/icons/cutraft.icns'),
        '--add-data', str(app / 'cutraft/resources') + ':cutraft/resources',
        '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build/mac'),
        '--specpath', str(ROOT / 'build')]
    for name in ('camera','tracker','tanuki'):
        # Authored Blender sources are not needed at runtime.
        if name == 'tanuki':
            for filename in ('tanuki.mesh.json.gz','tanuki.glb','tanuki_basecolor.png','goggle_lens.png'):
                args += ['--add-data', str(ROOT/'studio/assets'/name/filename) + ':studio/assets/'+name]
        else:
            args += ['--add-data', str(ROOT/'studio/assets'/name) + ':studio/assets/'+name]
    args += ['--add-binary', str(native/'screen-recorder')+':studio/native','--add-binary',str(native/'desktop-audio')+':studio/native','--add-binary',str(native/'live-window')+':studio/native']
    for module in ('PySide6.QtWebEngineQuick', 'tkinter', 'unittest'):
        args += ['--exclude-module', module]
    subprocess.run([*args, str(ROOT / 'run.py')], cwd=ROOT, check=True)
    import plistlib
    bundle = ROOT / 'dist/Tanutsuna Studio.app'
    repair_webengine_layout(bundle)
    info = bundle / 'Contents/Info.plist'
    data = plistlib.loads(info.read_bytes())
    data.update({'CFBundleShortVersionString':VERSION,'CFBundleVersion':VERSION.replace('.',''),'NSCameraUsageDescription':'顔の向きと口の開閉をソフト内で追跡し、V体を動かします。',
        'NSMicrophoneUsageDescription':'マイクの声をPC音とは別の音声素材として収録します。',
        'NSScreenCaptureUsageDescription':'選択したゲームウィンドウまたは画面とPC音を配信・録画します。'})
    info.write_bytes(plistlib.dumps(data))
    shutil.copy2(ROOT/'docs/release-0.9.md',bundle/'Contents/Resources/ご利用ガイド.md')
    shutil.copytree(ROOT/'build/live-deps/av-17.1.0.dist-info/licenses',bundle/'Contents/Resources/licenses/PyAV',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    subprocess.run(['codesign','--force','--deep','--sign','-',str(bundle)],check=True)
    print('Mac検証用アプリ: ' + str(ROOT / 'dist/Tanutsuna Studio.app'))

if __name__ == '__main__':
    build()
