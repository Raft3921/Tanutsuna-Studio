"""Prepare release archives from the previous official builds and current source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'studio/version.py').read_text(encoding='utf-8').split('STUDIO_VERSION = "', 1)[1].split('"', 1)[0]
BASE_VERSION = '1.4.14'
BUILD = ROOT / 'build/release'
DIST = ROOT / 'dist'


def copy_ignore(folder, names):
    return [name for name in names if name in ('.DS_Store', '__pycache__') or name.endswith(('.pyc', '.blend', '.blend1'))]


def unpack_windows(path):
    destination = BUILD / 'base-windows'
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(destination)
    return destination


def pack_windows(base):
    current = BUILD / 'current-windows'
    shutil.rmtree(current, ignore_errors=True)
    shutil.copytree(base, current)
    shutil.copytree(ROOT / 'studio', current / 'app/studio', dirs_exist_ok=True, ignore=copy_ignore)
    shutil.copy2(ROOT / 'README.md', current / 'README.md')
    shutil.copy2(ROOT / f'docs/release-{VERSION}.md', current / 'ご利用ガイド.md')
    hashes = {str(file.relative_to(current / 'app')).replace('\\', '/'): hashlib.sha256(file.read_bytes()).hexdigest()
              for file in (current / 'app').rglob('*') if file.is_file()}
    manifest = {'studioVersion': VERSION, 'editorVersion': '0.5.1', 'editorSource': 'Cut Raft',
                'integration': 'native Qt; shared editor widgets and palette; isolated preferences', 'sha256': hashes}
    (current / 'studio-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_path = DIST / 'Tanutsuna-Studio-Windows11-x64.zip'
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.unlink(missing_ok=True)
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(current.rglob('*')):
            if file.is_file():
                info = zipfile.ZipInfo(file.relative_to(current).as_posix())
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | (stat.S_IMODE(file.stat().st_mode) or 0o644)) << 16
                archive.writestr(info, file.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    subprocess.run([sys.executable, str(ROOT / 'scripts/make_delta.py'), '--base', str(base), '--current', str(current),
                    '--output', str(DIST / f'Tanutsuna-Studio-Windows11-x64-from-{BASE_VERSION}.zip'),
                    '--platform', 'Windows11-x64', '--from-version', BASE_VERSION, '--version', VERSION], check=True)
    print(f'Windows package ready: {archive_path}')
    return current


def extract_macos(path):
    destination = BUILD / 'base-macos'
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    subprocess.run(['/usr/bin/ditto', '-x', '-k', str(path), str(destination)], check=True)
    app = destination / 'Tanutsuna Studio.app'
    if not app.is_dir():
        raise RuntimeError('1.4.14 Mac archive has no Tanutsuna Studio.app')
    return app


def prepare(base_windows_zip, base_macos_zip):
    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    base_windows = unpack_windows(base_windows_zip)
    extract_macos(base_macos_zip)
    cutraft = base_windows / 'app/cutraft'
    if not (cutraft / 'app.py').is_file():
        raise RuntimeError('Previous Windows package is missing app/cutraft/app.py')
    vendor = ROOT / 'vendor/cutraft-win/app/cutraft'
    shutil.rmtree(vendor.parent, ignore_errors=True)
    shutil.copytree(cutraft, vendor, ignore=copy_ignore)
    return pack_windows(base_windows)


def finish():
    base = BUILD / 'base-windows'
    base_mac = BUILD / 'base-macos/Tanutsuna Studio.app'
    current_windows = BUILD / 'current-windows'
    current_mac = DIST / 'Tanutsuna Studio.app'
    for path in (base, base_mac, current_windows, current_mac):
        if not path.exists():
            raise RuntimeError(f'Release staging file is missing: {path}')
    mac_zip = DIST / 'Tanutsuna-Studio-macOS-arm64.zip'
    subprocess.run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(current_mac), str(mac_zip)], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/make_delta.py'), '--base', str(base_mac), '--current', str(current_mac),
                    '--output', str(DIST / f'Tanutsuna-Studio-macOS-arm64-from-{BASE_VERSION}.zip'),
                    '--platform', 'macOS-arm64', '--from-version', BASE_VERSION, '--version', VERSION], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/build_installers.py')], check=True)
    print('Mac, delta, and installer packages ready.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-windows', type=Path)
    parser.add_argument('--base-macos', type=Path)
    parser.add_argument('--finish', action='store_true')
    args = parser.parse_args()
    if args.finish:
        finish()
    elif args.base_windows and args.base_macos:
        prepare(args.base_windows, args.base_macos)
    else:
        parser.error('provide --base-windows and --base-macos, or --finish')


if __name__ == '__main__':
    main()
