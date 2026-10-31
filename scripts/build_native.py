"""Build the native Windows app using the existing Cut Raft runtime, without Electron."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import zipfile
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'studio/version.py').read_text(encoding='utf-8').split('STUDIO_VERSION = "', 1)[1].split('"', 1)[0]
SOURCE = Path(os.environ.get('CUTRAFT_SOURCE', '/Users/Raft/Documents/Cut Raft'))
BASE = SOURCE / 'dist/Cut-Raft-Windows11-x64'
VENDOR = ROOT / 'vendor/cutraft-win'
DESTINATION = ROOT / 'dist/Tanutsuna-Studio-Windows11-x64'


def ignore(folder, names):
    folder = Path(folder)
    return [n for n in names if n in ('.DS_Store', '__pycache__', 'whisper-mac')
            or (n == 'speech' and folder != ROOT / 'studio' / 'assets')
            or n.endswith(('.pyc', '.blend', '.blend1', '.bin'))
            or n in ('screen-recorder','desktop-audio','live-window')]


def prepare():
    if not (BASE / 'Cut Raft.exe').is_file():
        raise RuntimeError('元のCut Raft Windows配布物が必要です: ' + str(BASE))
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    shutil.copytree(BASE, VENDOR, ignore=ignore)
    # Include a bundled web view so camera tracking runs inside Studio.
    webengine_cache=ROOT/'build/windows-camera';webengine_cache.mkdir(parents=True,exist_ok=True)
    webengine_wheel=webengine_cache/'pyside6_webengine-6.12.0.140-cp310-abi3-win_amd64.whl'
    if not webengine_wheel.is_file():
        subprocess.run([sys.executable,'-m','pip','download','--only-binary=:all:','--no-deps','--platform','win_amd64','--python-version','314','--implementation','cp','--abi','cp314','--dest',str(webengine_cache),'pyside6_webengine==6.12.0.140'],check=True)
    with zipfile.ZipFile(webengine_wheel) as package:
        package.extractall(VENDOR/'runtime/Lib/site-packages')
    # PyAV and FFmpeg runtime are packaged; clients need no Python installation.
    live_cache=ROOT/'build/windows-live';live_cache.mkdir(parents=True,exist_ok=True)
    live_wheel=live_cache/'av-17.1.0-cp311-abi3-win_amd64.whl'
    if not live_wheel.is_file():
        subprocess.run([sys.executable,'-m','pip','download','--only-binary=:all:','--no-deps','--platform','win_amd64','--python-version','314','--implementation','cp','--abi','cp314','--dest',str(live_cache),'av==17.1.0'],check=True)
    with zipfile.ZipFile(live_wheel) as package:
        package.extractall(VENDOR/'runtime/Lib/site-packages')
    copied = VENDOR / 'app/cutraft'
    if copied.exists():
        shutil.rmtree(copied)
    shutil.copytree(SOURCE / 'cutraft', copied, ignore=ignore)
    sys.path.insert(0, str(ROOT))
    from editor_overrides.source_patches import apply_source_patches
    apply_source_patches(copied)
    # Isolate this app's preferences and default folders from the original Cut Raft.
    for name in ['app.py', 'workspace.py']:
        file = copied / name
        text = file.read_text(encoding='utf-8')
        text = text.replace('QSettings("CutRaft", "CutRaft")', 'QSettings("TanutsunaStudio", "Editor")')
        text = text.replace("QSettings('CutRaft', 'CutRaft')", "QSettings('TanutsunaStudio', 'Editor')")
        if name == 'workspace.py':
            text = text.replace(" / 'Cut Raft'", " / 'TanutsunaStudio'")
        else:
            text = text.replace('app.setApplicationName("Cut Raft")', 'app.setApplicationName("Tanutsuna Studio")')
            text = text.replace('app.setOrganizationName("CutRaft")', 'app.setOrganizationName("TanutsunaStudio")')
        text = text.replace('QApplication.instance().installEventFilter(self)', '# Studio uses widget key events; no global Python event filter')
        file.write_text(text, encoding='utf-8')
    for override in (ROOT / 'editor_overrides').glob('*.py'):
        shutil.copy2(override, copied / override.name)
    print('Cut Raftのソースと実行環境を準備しました。元プロジェクトは変更していません。')


def build():
    prepare()
    if DESTINATION.exists():
        shutil.rmtree(DESTINATION)
    shutil.copytree(VENDOR, DESTINATION, ignore=ignore)
    base_manifest = DESTINATION / 'build-manifest.json'
    if base_manifest.exists():
        base_manifest.rename(DESTINATION / 'CutRaft-base-build-manifest.json')
    shutil.copytree(ROOT / 'studio', DESTINATION / 'app/studio', ignore=ignore)
    # The existing native launcher waits for pythonw.exe and forwards command-line arguments.
    (DESTINATION / 'Cut Raft.exe').rename(DESTINATION / 'Tanutsuna Studio.exe')
    (DESTINATION / 'app/launch.py').write_text('''from pathlib import Path
import os, sys, traceback
folder = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'TanutsunaStudio'
folder.mkdir(parents=True, exist_ok=True)
if '--studio-worker' not in sys.argv:
    log = (folder / 'startup.log').open('w', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log
try:
    if '--studio-worker' in sys.argv:
        from studio.worker import main
        sys.exit(main(sys.argv[sys.argv.index('--studio-worker')+1:]))
    from studio.main import main
    sys.exit(main())
except Exception:
    traceback.print_exc()
    if '--studio-worker' in sys.argv:
        sys.exit(1)
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, f'起動エラーの詳細：\\n{folder / "startup.log"}', 'Tanutsuna Studio', 16)
    raise
''', encoding='utf-8')
    for name in ['README.md', 'WINDOWS.md', 'SHORTCUTS.md', 'pyproject.toml']:
        shutil.copy2(SOURCE / name, DESTINATION / ('CutRaft-' + name))
    shutil.copy2(ROOT / 'README.md', DESTINATION / 'README.md')
    shutil.copy2(ROOT / f'docs/release-{VERSION}.md', DESTINATION / 'ご利用ガイド.md')
    (DESTINATION / 'お読みください.txt').write_text('たぬつな Studio\n\nZIPをすべて展開し、「Tanutsuna Studio.exe」を開いてください。\napp・runtimeフォルダは移動しないでください。\nPythonやGitの準備は不要です。\nWindows 11 x64向けの未署名版です。\n', encoding='utf-8')
    hashes = {str(f.relative_to(DESTINATION)).replace('\\', '/'): hashlib.sha256(f.read_bytes()).hexdigest()
              for f in (DESTINATION / 'app').rglob('*') if f.is_file()}
    manifest = {'studioVersion': VERSION, 'editorVersion': '0.5.1', 'editorSource': 'Cut Raft',
        'integration': 'native Qt; shared editor widgets and palette; isolated preferences', 'sha256': hashes}
    (DESTINATION / 'studio-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = ROOT / 'dist/Tanutsuna-Studio-Windows11-x64.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for file in sorted(DESTINATION.rglob('*')):
            if file.is_file():
                z.write(file, str(file.relative_to(DESTINATION)))
    print(f'Windows配布ZIP: {archive} ({archive.stat().st_size:,} bytes)')


if __name__ == '__main__':
    import sys
    prepare() if '--prepare-only' in sys.argv else build()
