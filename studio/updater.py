"""Release checker and transactional installer for packaged builds."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import stat
import urllib.error
import urllib.request
import uuid
import zipfile
import plistlib
from pathlib import PurePosixPath
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .version import GITHUB_REPOSITORY, STUDIO_VERSION

API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
MAX_RELEASE_BYTES = 1024 * 1024 * 1024


def _version(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})", str(value).strip())
    if not match:
        raise ValueError("更新の版数が正しくありません。")
    return tuple(int(part) for part in match.group(1).split("."))


def packaged_platform() -> str | None:
    if sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64"):
        return "macOS-arm64"
    if sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"):
        return "Windows11-x64"
    return None


def installation_root() -> Path | None:
    """Return the app bundle/folder for supported frozen and onedir launches."""
    if getattr(sys, "frozen", False) and sys.platform == "darwin":
        for parent in Path(sys.executable).resolve().parents:
            if parent.suffix == ".app":
                return parent
    if sys.platform == "win32":
        script = Path(sys.argv[0]).resolve()
        if script.name.lower() == "launch.py" and script.parent.name.lower() == "app":
            return script.parent.parent
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
    return None


def request_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Tanutsuna-Studio-Updater",
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read(2 * 1024 * 1024))


def latest_release() -> dict:
    release = request_json(API)
    tag = release.get("tag_name", "")
    latest = _version(tag)
    current = _version(STUDIO_VERSION)
    if latest <= current:
        return {"available": False, "version": tag or STUDIO_VERSION}
    wanted = f"Tanutsuna-Studio-{packaged_platform()}.zip"
    if not packaged_platform():
        return {"available": True, "version": tag, "unsupported": True,
                "url": release.get("html_url", "")}
    asset = next((item for item in release.get("assets", []) if item.get("name") == wanted), None)
    if not asset:
        raise RuntimeError(f"Release {tag} に {wanted} がありません。更新を安全に適用できません。")
    digest = asset.get("digest", "")
    if not digest.startswith("sha256:"):
        raise RuntimeError("配布ファイルにSHA-256検証情報がありません。更新を停止しました。")
    return {"available": True, "version": tag, "url": asset["browser_download_url"],
            "size": int(asset.get("size", 0)), "sha256": digest[7:],
            "asset": wanted, "release_url": release.get("html_url", "")}


def _emit_progress(progress, value: int, message: str) -> None:
    if progress:
        progress(max(0, min(100, int(value))), message)


def _safe_zip_entries(archive: zipfile.ZipFile, allow_symlinks: bool) -> list[tuple[zipfile.ZipInfo, str, bool]]:
    """Reject traversal/special files while allowing contained Mac app links."""
    entries = []
    seen = set()
    symlinks = set()
    for member in archive.infolist():
        raw = member.filename
        if "\\" in raw or "\x00" in raw:
            raise RuntimeError("更新ZIPに不正なファイル名があります。")
        path = PurePosixPath(raw)
        parts = tuple(part for part in path.parts if part not in ("", "."))
        if path.is_absolute() or not parts or any(part == ".." for part in path.parts) or (parts and ":" in parts[0]):
            raise RuntimeError("更新ZIPに許可されないパスが含まれています。")
        normalized = "/".join(parts)
        mode = member.external_attr >> 16
        kind = stat.S_IFMT(mode)
        is_link = stat.S_ISLNK(mode)
        is_dir = member.is_dir() or stat.S_ISDIR(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
            raise RuntimeError("更新ZIPに特殊なファイルが含まれています。")
        if is_link:
            if not allow_symlinks:
                raise RuntimeError("この更新ZIPではシンボリックリンクを使用できません。")
            target_text = archive.read(member).decode("utf-8")
            target = PurePosixPath(target_text)
            if not target_text or target.is_absolute() or "\\" in target_text or "\x00" in target_text:
                raise RuntimeError("更新ZIPに許可されないリンク先が含まれています。")
            stack = list(parts[:-1])
            for part in target.parts:
                if part in ("", "."):
                    continue
                if part == "..":
                    if not stack:
                        raise RuntimeError("更新ZIPのリンク先がアプリの外を指しています。")
                    stack.pop()
                else:
                    stack.append(part)
            symlinks.add(normalized)
        if normalized in seen:
            raise RuntimeError("更新ZIPに重複するファイル名があります。")
        seen.add(normalized)
        entries.append((member, normalized, is_link))
    for _member, name, _is_link in entries:
        parents = PurePosixPath(name).parents
        if any(str(parent) in symlinks for parent in parents):
            raise RuntimeError("更新ZIPがシンボリックリンクの内側へファイルを書き込みます。")
    return entries


def _verify_zip_crc(archive: zipfile.ZipFile, entries, progress=None) -> None:
    files = [(member, name) for member, name, is_link in entries
             if not is_link and not member.is_dir() and member.file_size]
    total = sum(member.file_size for member, _ in files) or 1
    checked = 0
    last = -1
    for member, name in files:
        with archive.open(member) as source:
            while chunk := source.read(1024 * 1024):
                checked += len(chunk)
                value = 72 + int(19 * checked / total)
                if value != last:
                    _emit_progress(progress, value, f"ZIPを検証中: {name}")
                    last = value


def _download(release: dict, destination: Path, progress=None) -> None:
    size = release["size"]
    if size <= 0 or size > MAX_RELEASE_BYTES:
        raise RuntimeError("配布ファイルのサイズが許容範囲外です。")
    request = urllib.request.Request(release["url"], headers={
        "Accept": "application/octet-stream", "User-Agent": "Tanutsuna-Studio-Updater"})
    digest = hashlib.sha256()
    received = 0
    last = -1
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            received += len(chunk)
            if received > size or received > MAX_RELEASE_BYTES:
                raise RuntimeError("配布ファイルの実サイズが宣言値を超えました。")
            output.write(chunk)
            digest.update(chunk)
            value = int(70 * received / size)
            if value != last:
                _emit_progress(progress, value, f"更新をダウンロード中: {received // (1024 * 1024)} / {size // (1024 * 1024)} MB")
                last = value
    if received != size or digest.hexdigest().lower() != release["sha256"].lower():
        destination.unlink(missing_ok=True)
        raise RuntimeError("ダウンロードの検証に失敗しました。現在のアプリは変更していません。")
    _emit_progress(progress, 71, "ダウンロードのSHA-256を確認中…")
    with zipfile.ZipFile(destination) as archive:
        is_mac = packaged_platform() == "macOS-arm64"
        entries = _safe_zip_entries(archive, allow_symlinks=is_mac)
        _verify_zip_crc(archive, entries, progress)
        names = archive.namelist()
        if packaged_platform() == "Windows11-x64":
            if "Tanutsuna Studio.exe" not in names or "studio-manifest.json" not in names:
                raise RuntimeError("Windows更新ZIPの構成が正しくありません。")
            manifest = json.loads(archive.read("studio-manifest.json"))
            if manifest.get("studioVersion") != release["version"].lstrip("v"):
                raise RuntimeError("Windows更新ZIPの版数が一致しません。")
            manifest_items = list(manifest.get("sha256", {}).items())
            for index, (name, expected) in enumerate(manifest_items):
                try:
                    actual = hashlib.sha256(archive.read(name)).hexdigest()
                except KeyError as exc:
                    raise RuntimeError(f"更新ZIPに必要なファイルがありません: {name}") from exc
                if actual.lower() != expected.lower():
                    raise RuntimeError(f"Windowsアプリの検証に失敗しました: {name}")
                if manifest_items:
                    _emit_progress(progress, 91 + int(8 * (index + 1) / len(manifest_items)), f"アプリ構成を検証中: {index + 1} / {len(manifest_items)}")
        else:
            app_dirs = {name.split("/")[0] for name in names if name.endswith(".app/Contents/Info.plist")}
            if len(app_dirs) != 1:
                raise RuntimeError("Mac更新ZIPにアプリ本体が1つ含まれていません。")
            app_root = next(iter(app_dirs))
            plist_path = f"{app_root}/Contents/Info.plist"
            if plist_path not in names:
                raise RuntimeError("Mac更新アプリのInfo.plistがありません。")
            info = plistlib.loads(archive.read(plist_path))
            if info.get("CFBundleShortVersionString") != release["version"].lstrip("v"):
                raise RuntimeError("Mac更新アプリの版数が一致しません。")
            _emit_progress(progress, 99, "Macアプリとリンク構成を確認しました。")
    _emit_progress(progress, 100, "更新ファイルの検証が完了しました。")


def _extract_windows(archive: zipfile.ZipFile, stage: Path, entries, progress=None, start=0, end=95) -> None:
    files = [(member, name) for member, name, is_link in entries if not is_link]
    total = sum(member.file_size for member, _ in files) or 1
    copied = 0
    last = -1
    for member, name in files:
        target = stage.joinpath(*PurePosixPath(name).parts)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
                copied += len(chunk)
                value = start + int((end - start) * copied / total)
                if value != last:
                    _emit_progress(progress, value, f"更新ファイルを展開中: {copied // (1024 * 1024)} / {total // (1024 * 1024)} MB")
                    last = value
        mode = member.external_attr >> 16
        if mode and stat.S_ISREG(mode):
            target.chmod(mode & 0o777)


def _extract_mac(package: Path, stage: Path, progress=None, start=0, end=95) -> None:
    """Use ditto so app-bundle links, executable bits, and resource forks survive."""
    process = subprocess.Popen(["/usr/bin/ditto", "-V", "-x", "-k", str(package), str(stage)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               bufsize=1)
    with zipfile.ZipFile(package) as archive:
        total = sum(1 for member in archive.infolist()
                    if not member.is_dir() and not member.filename.startswith("__MACOSX/")
                    and not ("/._" in member.filename)) or 1
    completed = 0
    last = -1
    assert process.stdout is not None
    for _line in process.stdout:
        completed += 1
        value = min(end, start + int((end - start) * completed / total))
        if value != last:
            _emit_progress(progress, value, f"Macアプリを展開中: {min(completed, total)} / {total} ファイル")
            last = value
    code = process.wait()
    if code:
        raise RuntimeError(f"macOS標準のアーカイブ展開に失敗しました（終了コード {code}）。")
    _emit_progress(progress, end + 1, "Macアプリの実行属性とリソースを復元しました。")


def _data_root() -> Path:
    from PySide6.QtCore import QStandardPaths
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))


def install_and_restart(release: dict, package: Path, current_pid: int, progress=None) -> None:
    target = installation_root()
    if target is None:
        raise RuntimeError("この起動方式は自動更新に対応していません。Releaseページから更新してください。")
    if not package.is_file():
        raise RuntimeError("検証済みの更新ファイルが見つかりません。先に更新をダウンロードしてください。")
    total = max(1, package.stat().st_size)
    digest = hashlib.sha256();checked = 0;last = -1
    with package.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk);checked += len(chunk)
            value = int(5 * checked / total)
            if value != last:
                _emit_progress(progress, value, "適用前に更新ファイルを再確認中…")
                last = value
    if digest.hexdigest().lower() != release.get("sha256", "").lower():
        raise RuntimeError("保存済み更新ファイルが変更されています。もう一度ダウンロードしてください。")
    token = uuid.uuid4().hex
    data = _data_root()
    data.mkdir(parents=True, exist_ok=True)
    work = data / "updates" / token
    work.mkdir(parents=True, exist_ok=False)
    stage = work / "stage"
    stage.mkdir()
    with zipfile.ZipFile(package) as archive:
        is_mac = packaged_platform() == "macOS-arm64"
        entries = _safe_zip_entries(archive, allow_symlinks=is_mac)
        if is_mac:
            _extract_mac(package, stage, progress, start=5, end=95)
        else:
            _extract_windows(archive, stage, entries, progress, start=5, end=95)
    marker = data / "update-boot.json"
    marker.unlink(missing_ok=True)
    if sys.platform == "darwin":
        app_name = next(p.name for p in stage.iterdir() if p.is_dir() and p.suffix == ".app")
        candidate = stage / app_name
        if app_name != target.name:
            raise RuntimeError(f"更新アプリ名が現在のアプリと異なります: {app_name}")
        helper = work / "install-update.sh"
        helper.write_text("""#!/bin/sh
set -eu
target=$1; candidate=$2; backup=$3; marker=$4; token=$5; pid=$6; work=$7
tries=0
while kill -0 "$pid" 2>/dev/null && [ "$tries" -lt 90 ]; do sleep 1; tries=$((tries+1)); done
contents="$target/Contents"; incoming="$target/.Contents-update-$token"; backup_contents="$target/.Contents-previous-$token"
if [ ! -d "$contents" ] || [ ! -w "$target" ]; then open -a "$target"; exit 1; fi
if ! ditto "$candidate/Contents" "$incoming"; then rm -rf "$incoming"; open -a "$target"; exit 1; fi
if ! mv "$contents" "$backup_contents"; then rm -rf "$incoming"; open -a "$target"; exit 1; fi
if ! mv "$incoming" "$contents"; then mv "$backup_contents" "$contents"; open -a "$target"; exit 1; fi
open -a "$target" --args --update-token "$token"
tries=0
while [ "$tries" -lt 90 ]; do
  if [ -f "$marker" ] && grep -q "$token" "$marker"; then
    rm -rf "$backup_contents" "$work"
    for prior in "$(dirname "$work")"/*; do
      [ "$prior" = "$work" ] && continue
      [ -f "$prior/install-update.sh" ] && rm -rf "$prior"
    done
    exit 0
  fi
  sleep 1; tries=$((tries+1))
done
rm -rf "$contents"
[ ! -e "$backup_contents" ] || mv "$backup_contents" "$contents"
open -a "$target"
exit 2
""", encoding="utf-8")
        helper.chmod(0o700)
        _emit_progress(progress, 99, "アプリの入れ替えと再起動を準備中…")
        subprocess.Popen(["/bin/sh", str(helper), str(target), str(candidate), str(target)+".previous-"+token,
                          str(marker), token, str(current_pid), str(work)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
    else:
        candidate = stage
        helper = work / "install-update.ps1"
        helper.write_text(r'''param($Target,$Stage,$Backup,$Marker,$Token,[int]$Pid,[string]$Work)
$ErrorActionPreference = 'Stop'
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Process -Id $Pid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
try {
  if (Test-Path $Backup) { Remove-Item $Backup -Recurse -Force }
  Move-Item -LiteralPath $Target -Destination $Backup
  New-Item -ItemType Directory -Path $Target | Out-Null
  Get-ChildItem -LiteralPath $Stage | Move-Item -Destination $Target
  Start-Process -FilePath (Join-Path $Target 'Tanutsuna Studio.exe') -ArgumentList @('--update-token',$Token)
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $deadline) {
    if ((Test-Path $Marker) -and ((Get-Content -Raw $Marker) -like "*$Token*")) { Remove-Item $Backup,$Work -Recurse -Force; exit 0 }
    Start-Sleep -Seconds 1
  }
  throw 'Updated app did not report successful startup'
} catch {
  if (Test-Path $Backup) {
    if (Test-Path $Target) { Remove-Item $Target -Recurse -Force }
    Move-Item -LiteralPath $Backup -Destination $Target
  }
  if (Test-Path (Join-Path $Target 'Tanutsuna Studio.exe')) { Start-Process (Join-Path $Target 'Tanutsuna Studio.exe') }
  exit 2
}
''', encoding="utf-8")
        _emit_progress(progress, 99, "アプリの入れ替えと再起動を準備中…")
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper),
                          str(target), str(candidate), str(target)+".previous-"+token,
                          str(marker), token, str(current_pid), str(work)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)


class UpdateWorker(QThread):
    completed = Signal(object)
    progress = Signal(int, str)

    def __init__(self, action: str, release: dict | None = None, package: Path | None = None):
        super().__init__()
        self.action, self.release, self.package = action, release, package

    def run(self):
        try:
            if self.action == "check":
                result = latest_release()
            elif self.action == "download":
                if self.release is None:
                    raise RuntimeError("更新情報がありません。先に更新を確認してください。")
                folder = _data_root() / "updates" / "download"
                folder.mkdir(parents=True, exist_ok=True)
                package = folder / self.release["asset"]
                _download(self.release, package, self.progress.emit)
                result = {"downloaded": True, "package": str(package), "version": self.release["version"]}
            elif self.action == "install":
                install_and_restart(self.release, self.package, os.getpid(), self.progress.emit)
                result = {"restarting": True}
            else:
                raise ValueError("Unknown update action")
        except Exception as exc:
            result = {"error": str(exc)}
        self.completed.emit(result)
