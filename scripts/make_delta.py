"""Create a file-level update package between two unpacked Studio builds."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile


def inventory(root: Path, allow_symlinks: bool):
    result = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        folder = Path(folder)
        for name in list(dirs):
            path = folder / name
            if name == '.DS_Store':
                dirs.remove(name)
                continue
            if path.is_symlink():
                dirs.remove(name)
                files.append(name)
        for name in files:
            if name == '.DS_Store':
                continue
            path = folder / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                if not allow_symlinks:
                    raise RuntimeError('Windows差分パッケージではシンボリックリンクを扱えません。完全版を使用してください。')
                payload = os.readlink(path).encode('utf-8')
                kind = 'symlink'
            elif stat.S_ISREG(info.st_mode):
                payload = None
                kind = 'file'
            else:
                raise RuntimeError(f'サポートしていないファイル形式です: {path}')
            digest = hashlib.sha256(payload if payload is not None else path.read_bytes()).hexdigest()
            result[relative] = {'path': path, 'sha256': digest, 'mode': mode, 'type': kind}
    return result


def create(base: Path, current: Path, output: Path, platform: str, from_version: str, version: str):
    allow_symlinks = platform == 'macOS-arm64'
    old, new = inventory(base, allow_symlinks), inventory(current, allow_symlinks)
    changed = {}
    for name, record in new.items():
        prior = old.get(name)
        signature = (record['sha256'], record['type'])
        previous_signature = None if prior is None else (prior['sha256'], prior['type'])
        if platform == 'macOS-arm64':
            signature += (record['mode'],)
            if prior is not None:
                previous_signature += (prior['mode'],)
        if previous_signature != signature:
            changed[name] = {key: record[key] for key in ('sha256', 'mode', 'type')}
    deleted = sorted(set(old) - set(new))
    manifest = {'format': 'tanutsuna-delta-v1', 'platform': platform,
                'from': from_version, 'version': version,
                'files': changed, 'delete': deleted}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('delta-manifest.json', json.dumps(manifest, ensure_ascii=False,
            sort_keys=True, separators=(',', ':')))
        for relative, row in changed.items():
            path = new[relative]['path']
            if row['type'] == 'symlink':
                payload = os.readlink(path).encode('utf-8')
            else:
                payload = path.read_bytes()
            entry = zipfile.ZipInfo(relative)
            entry.create_system = 3
            entry.external_attr = ((stat.S_IFLNK if row['type'] == 'symlink' else stat.S_IFREG) |
                                   row['mode']) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    print(json.dumps({'base': from_version, 'version': version, 'platform': platform,
        'changed': len(changed), 'deleted': len(deleted), 'bytes': output.stat().st_size,
        'path': str(output)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--current', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--platform', required=True, choices=('macOS-arm64', 'Windows11-x64'))
    parser.add_argument('--from-version', required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    create(args.base, args.current, args.output, args.platform, args.from_version, args.version)


if __name__ == '__main__':
    main()
