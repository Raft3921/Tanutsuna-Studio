"""Build a self-contained Mac verification app using this Mac's Python runtime."""
from pathlib import Path
import os
import subprocess
import sys


def restore_webengine_framework_layout(bundle):
    import PySide6
    import shutil
    source_root=Path(PySide6.__file__).parent/'Qt/lib'
    target_root=bundle/'Contents/Frameworks/PySide6/Qt/lib'
    for name in ('QtWebEngineCore','QtWebEngineWidgets','QtWebEngineQuick','QtWebEngineQuickDelegatesQml'):
        source=source_root/(name+'.framework')
        target=target_root/(name+'.framework')
        version=target/'Versions/A'
        version.mkdir(parents=True,exist_ok=True)
        binary=source/'Versions/A'/name
        if not binary.is_file():raise RuntimeError('Qt WebEngine framework が見つかりません: '+str(binary))
        shutil.copy2(binary,version/name)
        for folder in ('Resources','Helpers'):
            for origin in (source/'Versions/A'/folder,source/folder):
                if origin.is_dir():shutil.copytree(origin,version/folder,dirs_exist_ok=True,symlinks=True)
        versions=target/'Versions'
        current=versions/'Current'
        if not current.exists() and not current.is_symlink():os.symlink('A',current)
        for leaf,destination in ((name,'Versions/Current/'+name),('Resources','Versions/Current/Resources'),('Helpers','Versions/Current/Helpers')):
            link=target/leaf
            if not link.exists() and not link.is_symlink():os.symlink(destination,link)

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'studio/version.py').read_text(encoding='utf-8').split('STUDIO_VERSION = "', 1)[1].split('"', 1)[0]
NUMPY_VERSION = '2.5.4'
IMAGEIO_FFMPEG_VERSION = '0.6.0'

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
    try:
        import numpy
        if numpy.__version__ != NUMPY_VERSION:
            raise ImportError(f'NumPy {NUMPY_VERSION} が必要です')
    except ImportError:
        subprocess.run([sys.executable,'-m','pip','install',f'numpy=={NUMPY_VERSION}'],check=True)
    try:
        import imageio_ffmpeg
        if imageio_ffmpeg.__version__ != IMAGEIO_FFMPEG_VERSION:
            raise ImportError(f'imageio-ffmpeg {IMAGEIO_FFMPEG_VERSION} が必要です')
    except ImportError:
        subprocess.run([sys.executable,'-m','pip','install',f'imageio-ffmpeg=={IMAGEIO_FFMPEG_VERSION}'],check=True)
    if not (app / 'cutraft/app.py').is_file():
        raise SystemExit('先に scripts/build_native.py --prepare-only を実行してください。')
    # Reapply the maintained fixes without copying the Windows runtime.
    import shutil
    for source in (ROOT / 'editor_overrides').glob('*.py'):
        shutil.copy2(source, app / 'cutraft' / source.name)
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
        '--name', 'Tanutsuna Studio', '--osx-bundle-identifier', 'com.tanutsuna.studio',
        '--paths', str(app), '--paths',str(ROOT/'build/live-deps'),'--collect-all','av','--collect-all','numpy','--collect-all','imageio_ffmpeg',
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
    args += ['--add-data',str(ROOT/'studio/assets/tanutsuna-studio-icon.png')+':studio/assets']
    args += ['--add-binary', str(native/'screen-recorder')+':studio/native','--add-binary',str(native/'desktop-audio')+':studio/native','--add-binary',str(native/'live-window')+':studio/native', '--add-binary',str(native/'whisper-mac/whisper-cli')+':studio/native/whisper-mac', '--add-data',str(ROOT/'studio/assets/speech/WHISPER-LICENSE.txt')+':studio/native']
    for module in ('tkinter', 'unittest'):
        args += ['--exclude-module', module]
    subprocess.run([*args, str(ROOT / 'run.py')], cwd=ROOT, check=True)
    import plistlib
    bundle = ROOT / 'dist/Tanutsuna Studio.app'
    info = bundle / 'Contents/Info.plist'
    data = plistlib.loads(info.read_bytes())
    data.update({'CFBundleShortVersionString':VERSION,'CFBundleVersion':VERSION.replace('.',''),
        # QtWebEngine's helper process runs as a separately sandboxed child.
        # Keep automatic GPU switching enabled for WebEngine views on Macs.
        'NSSupportsAutomaticGraphicsSwitching':True,
        'NSMicrophoneUsageDescription':'マイクの声をPC音とは別の音声素材として収録し、V体の動きに反映します。',
        'NSCameraUsageDescription':'V体を動かすため、顔の向きと表情をこのPC内で解析します。映像は保存・送信しません。',
        'NSScreenCaptureUsageDescription':'選択したゲームウィンドウまたは画面とPC音を配信・録画します。'})
    info.write_bytes(plistlib.dumps(data))
    shutil.copy2(ROOT/f'docs/release-{VERSION}.md',bundle/'Contents/Resources/ご利用ガイド.md')
    shutil.copytree(ROOT/'build/live-deps/av-17.1.0.dist-info/licenses',bundle/'Contents/Resources/licenses/PyAV',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    restore_webengine_framework_layout(bundle)
    # `--deep` alone signs QtWebEngineProcess.app without the entitlements it
    # needs for Chromium's JIT and sandbox. It then aborts in QWebEnginePage's
    # constructor when live face tracking creates its first embedded page.
    # Sign the nested code first, then the helper with Qt's shipped entitlements,
    # and finally seal its containing framework and the outer app.
    subprocess.run(['codesign','--force','--deep','--sign','-',str(bundle)],check=True)
    framework=bundle/'Contents/Frameworks/PySide6/Qt/lib/QtWebEngineCore.framework'
    helper=framework/'Versions/A/Helpers/QtWebEngineProcess.app'
    entitlements=helper/'Contents/Resources/QtWebEngineProcess.entitlements'
    if not helper.is_dir() or not entitlements.is_file():
        raise RuntimeError('QtWebEngineProcess または sandbox entitlements が見つかりません。')
    subprocess.run(['codesign','--force','--sign','-','--entitlements',str(entitlements),str(helper)],check=True)
    subprocess.run(['codesign','--force','--sign','-',str(framework)],check=True)
    subprocess.run(['codesign','--force','--sign','-',str(bundle)],check=True)
    print('Mac検証用アプリ: ' + str(ROOT / 'dist/Tanutsuna Studio.app'))

if __name__ == '__main__':
    build()
