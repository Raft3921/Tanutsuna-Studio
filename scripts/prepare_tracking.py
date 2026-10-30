"""Pin and bundle the official MediaPipe web runtime and face model for offline localhost use."""
from pathlib import Path
import hashlib
import io
import json
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'studio/assets/tracker'
VERSION = '0.10.32'


def download(url):
    with urllib.request.urlopen(url, timeout=90) as response:
        return response.read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    registry = json.loads(download(f'https://registry.npmjs.org/@mediapipe/tasks-vision/{VERSION}'))
    tarball = download(registry['dist']['tarball'])
    import base64
    actual = 'sha512-' + base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    assert actual == registry['dist']['integrity'], 'npm integrity mismatch'
    with tarfile.open(fileobj=io.BytesIO(tarball), mode='r:gz') as archive:
        for member in archive.getmembers():
            relative = Path(member.name).relative_to('package')
            if not member.isfile() or '..' in relative.parts:
                continue
            if str(relative) not in ('vision_bundle.mjs', 'package.json') and relative.parts[0] != 'wasm' and 'license' not in str(relative).lower():
                continue
            file = OUT / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(archive.extractfile(member).read())
    url = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'
    (OUT / 'face_landmarker.task').write_bytes(download(url))
    # Keep upstream license text alongside the runtime even if the npm archive omits it.
    if not any('license' in p.name.lower() for p in OUT.iterdir()):
        (OUT / 'LICENSE').write_bytes(download('https://raw.githubusercontent.com/google-ai-edge/mediapipe/master/LICENSE'))
    manifest = {'package': '@mediapipe/tasks-vision', 'version': VERSION,
        'runtime': registry['dist']['tarball'], 'model': url,
        'modelDocumentation': 'https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker',
        'sha256': {str(p.relative_to(OUT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.rglob('*') if p.is_file()}}
    (OUT / 'assets-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('MediaPipe runtime/model ready:', sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file()), 'bytes')


if __name__ == '__main__':
    main()
