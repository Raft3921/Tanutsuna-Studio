"""Checks the handoff package, not Windows execution."""
from pathlib import Path
import hashlib
import json
import struct
import zipfile

ROOT = Path(__file__).resolve().parents[1]
bundle = ROOT / 'dist/Tanutsuna-Studio-Windows11-x64'
archive = bundle.with_suffix('.zip')
for name in ['Tanutsuna Studio.exe', 'runtime/pythonw.exe', 'runtime/python314.dll']:
    data = (bundle / name).read_bytes()
    assert data[:2] == b'MZ', name
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    assert data[offset:offset + 4] == b'PE\0\0', name
    assert struct.unpack_from('<H', data, offset + 4)[0] == 0x8664, name
assert (bundle / 'runtime/python314._pth').read_text().splitlines() == ['python314.zip', '.', 'Lib/site-packages', '../app', 'import site']
assert list(bundle.rglob('ffmpeg-*.exe')), 'FFmpeg missing'
assert list((bundle / 'runtime/Lib/site-packages').glob('*.dist-info/licenses')), 'Licenses missing'
assert list((bundle / 'app/cutraft/resources/fonts').glob('*')), 'Fonts missing'
manifest = json.loads((bundle / 'studio-manifest.json').read_text(encoding='utf-8'))
for file, digest in manifest['sha256'].items():
    assert hashlib.sha256((bundle / file).read_bytes()).hexdigest() == digest, file
# Check the packaged snapshot; other work may add unrelated source modules later.
for name in ['__init__.py', 'main.py', 'desk.py', 'workspace.py']:
    assert (bundle / 'app/studio' / name).is_file(), name
for packaged in (bundle / 'app/studio').glob('*.py'):
    assert packaged.read_bytes() == (ROOT / 'studio' / packaged.name).read_bytes(), packaged
for source in (ROOT / 'editor_overrides').glob('*.py'):
    assert source.read_bytes() == (bundle / 'app/cutraft' / source.name).read_bytes(), source
assert 'from studio.main import main' in (bundle / 'app/launch.py').read_text(encoding='utf-8')
assert archive.stat().st_size < 450_000_000
for name in ['QtWebEngineCore.pyd','QtWebEngineWidgets.pyd','QtWebEngineProcess.exe','Qt6WebEngineCore.dll']:
    assert not (bundle/'runtime/Lib/site-packages/PySide6'/name).exists(),name
for name in ['app/studio/native/Loopback.cs','app/studio/native/LiveLoopback.cs','runtime/Lib/site-packages/av/__init__.py']:
    assert (bundle/name).is_file(), name
assert (bundle/'app/studio/assets/speech/WHISPER-LICENSE.txt').is_file(), 'Whisper license missing'
assert (bundle/'app/studio/native/whisper-win/whisper-cli.exe').is_file(), 'Whisper engine missing'
assert not list((bundle/'app/studio/assets/speech').glob('*.bin')), 'Optional speech model should download on demand'
with zipfile.ZipFile(archive) as zipped:
    assert zipped.testzip() is None
    for file in ['Tanutsuna Studio.exe', 'app/launch.py', 'app/studio/desk.py', 'app/cutraft/app.py', 'studio-manifest.json']:
        assert zipped.read(file) == (bundle / file).read_bytes(), file
print(f'PASS: Windows x64 PE, runtime paths, FFmpeg/fonts/licenses, native integration source hashes, ZIP CRC ({archive.stat().st_size:,} bytes).')
print('Windows execution was not tested.')
