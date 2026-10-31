"""Package tiny guided installers that fetch and verify the full release."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'studio/version.py').read_text(encoding='utf-8').split('STUDIO_VERSION = "', 1)[1].split('"', 1)[0]
REPOSITORY = 'Raft3921/Tanutsuna-Studio'


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(8 * 1024 * 1024):
            value.update(block)
    return value.hexdigest()


def build() -> Path:
    mac = ROOT / 'dist/Tanutsuna-Studio-macOS-arm64.zip'
    windows = ROOT / 'dist/Tanutsuna-Studio-Windows11-x64.zip'
    for path in (mac, windows):
        if not path.is_file():
            raise FileNotFoundError(f'先に両OSの完全版ZIPを作成してください: {path}')
    tag = f'v{VERSION}'
    mac_url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{mac.name}'
    win_url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{windows.name}'
    mac_hash, win_hash = digest(mac), digest(windows)

    mac_script = r'''#!/bin/zsh
set -euo pipefail
VERSION="__VERSION__"
URL="__URL__"
EXPECTED="__SHA256__"
SIZE=__SIZE__
APP_NAME="Tanutsuna Studio.app"
echo "たぬつな Studio __VERSION__ インストーラー"
echo "これからMac版をダウンロードし、SHA-256を確認してApplicationsへインストールします。"
echo "ダウンロード中は進捗が表示されます。作品・設定はアプリとは別に保存されます。"
echo "初回にMacがこのインストーラーを止めたら、Finderでcontrolキーを押しながらクリックし、「開く」を選んでください。"
CHOICE=$(/usr/bin/osascript -e 'display dialog "たぬつな Studio __VERSION__ をダウンロードしてインストールします。\n\nMac版のサイズは約465MBです。進捗を表示し、完了後にApplicationsへ入れます。" buttons {"キャンセル", "続ける"} default button "続ける" cancel button "キャンセル" with title "たぬつな Studio インストーラー"' -e 'button returned of result') || exit 0
[[ "$CHOICE" == "続ける" ]] || exit 0
TMP=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/tanutsuna-install.XXXXXX")
trap '/bin/rm -rf "$TMP"' EXIT
ZIP="$TMP/Tanutsuna-Studio-macOS-arm64.zip"
PROBE=$(/usr/bin/curl --location --silent --show-error --range 0-0 --output /dev/null --write-out '%{http_code} %{size_download}' "$URL")
if [[ "$PROBE" == "206 1" ]]; then
  PARTS="$TMP/parts"
  /bin/mkdir -p "$PARTS"
  BLOCK=2097152
  WORKERS=8
  COMPLETED=0
  BATCH=()
  BATCH_SIZES=()
  INDEX=0
  for ((START=0; START<SIZE; START+=BLOCK)); do
    END=$((START + BLOCK - 1))
    (( END >= SIZE )) && END=$((SIZE - 1))
    EXPECTED_SIZE=$((END - START + 1))
    PART=$(printf '%s/chunk.%04d' "$PARTS" "$INDEX")
    /usr/bin/curl --location --fail --retry 3 --silent --show-error --range "$START-$END" "$URL" --output "$PART" &
    BATCH+=("$!")
    BATCH_SIZES+=("$EXPECTED_SIZE")
    ((INDEX+=1))
    if (( ${#BATCH[@]} == WORKERS || END == SIZE - 1 )); then
      FAILED=0
      for PID in "${BATCH[@]}"; do wait "$PID" || FAILED=1; done
      (( FAILED == 0 )) || { echo "分割ダウンロードに失敗しました。もう一度実行してください。"; exit 1; }
      for ((I=1; I<=${#BATCH_SIZES[@]}; I++)); do
        PART=$(printf '%s/chunk.%04d' "$PARTS" $((INDEX - ${#BATCH_SIZES[@]} + I - 1)))
        ACTUAL_SIZE=$(/usr/bin/stat -f%z "$PART")
        [[ "$ACTUAL_SIZE" == "${BATCH_SIZES[$I]}" ]] || { echo "受信サイズが一致しません。もう一度実行してください。"; exit 1; }
        ((COMPLETED+=ACTUAL_SIZE))
      done
      printf '\rダウンロード中: %3d%%  (%d / %d MB)' $((COMPLETED * 100 / SIZE)) $((COMPLETED / 1048576)) $((SIZE / 1048576))
      BATCH=()
      BATCH_SIZES=()
    fi
  done
  printf '\nファイルを結合しています…\n'
  /bin/cat "$PARTS"/chunk.* > "$ZIP"
else
  echo "分割配信に未対応のため、通常の方法でダウンロードします。"
  /usr/bin/curl --location --fail --retry 3 --progress-bar "$URL" --output "$ZIP"
fi
[[ "$(/usr/bin/stat -f%z "$ZIP")" == "$SIZE" ]] || { echo "ダウンロードサイズが一致しません。"; exit 1; }
ACTUAL=$(/usr/bin/shasum -a 256 "$ZIP" | /usr/bin/awk '{print $1}')
if [[ "$ACTUAL" != "$EXPECTED" ]]; then
  echo "ダウンロードの確認に失敗しました。ファイルはインストールしていません。"
  /usr/bin/osascript -e 'display dialog "ダウンロードの確認に失敗しました。インストールは行っていません。通信を確認して再実行してください。" buttons {"OK"} with icon stop'
  exit 1
fi
echo "確認できました。アプリを展開しています…"
/usr/bin/ditto -x -k "$ZIP" "$TMP"
APP="$TMP/$APP_NAME"
if [[ ! -d "$APP" ]]; then
  echo "アプリ本体がZIP内にありません。"
  exit 1
fi
/usr/bin/xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
APP_DIR=/Applications
if [[ ! -w "$APP_DIR" ]]; then
  APP_DIR="$HOME/Applications"
  /bin/mkdir -p "$APP_DIR"
  echo "書き込み権限に合わせて $APP_DIR にインストールします。"
fi
DEST="$APP_DIR/$APP_NAME"
BACKUP="$APP_DIR/.Tanutsuna-Studio-previous-$$.app"
/usr/bin/osascript -e 'tell application "Tanutsuna Studio" to quit' >/dev/null 2>&1 || true
/bin/sleep 2
if [[ -e "$DEST" ]]; then
  /bin/mv "$DEST" "$BACKUP"
fi
if ! /bin/mv "$APP" "$DEST"; then
  [[ ! -e "$BACKUP" ]] || /bin/mv "$BACKUP" "$DEST"
  echo "インストールできませんでした。前のアプリは元の場所に戻しました。"
  exit 1
fi
[[ ! -e "$BACKUP" ]] || /bin/rm -rf "$BACKUP"
echo "インストールが完了しました。Studioを起動します。"
/usr/bin/open "$DEST"
'''.replace('__VERSION__', VERSION).replace('__URL__', mac_url).replace('__SHA256__', mac_hash).replace('__SIZE__', str(mac.stat().st_size))

    windows_script = r'''$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
$Version = '__VERSION__'
$Url = '__URL__'
$ExpectedSha256 = '__SHA256__'
$ExpectedSize = [int64]__SIZE__
$Programs = Join-Path $env:LOCALAPPDATA 'Programs'
$Install = Join-Path $Programs 'Tanutsuna Studio'
$Work = Join-Path $Programs ('.TanutsunaStudio-install-' + [guid]::NewGuid().ToString('N'))
$Zip = Join-Path $Work 'Tanutsuna-Studio-Windows11-x64.zip'
$Stage = Join-Path $Work 'unpacked'
try {
  Write-Host "たぬつな Studio $Version インストーラー" -ForegroundColor Cyan
  Write-Host 'Windows版をダウンロードし、確認後にユーザー領域へインストールします。'
  Write-Host 'ダウンロード中は進捗を表示します。作品・設定はアプリとは別に保存されます。'
  [void](Read-Host 'Enterキーで開始（キャンセルする場合はウィンドウを閉じてください）')
  New-Item -ItemType Directory -Force -Path $Work | Out-Null
  $Curl = Join-Path $env:SystemRoot 'System32\curl.exe'
  $Probe = & $Curl --location --silent --show-error --range '0-0' --output 'NUL' --write-out '%{http_code} %{size_download}' $Url
  if ($LASTEXITCODE -ne 0 -or "$Probe" -ne '206 1') {
    Write-Host '配信元が分割ダウンロードに対応していないため、通常の方法で取得します。'
    & $Curl --location --fail --retry 3 --progress-bar --output $Zip $Url
    if ($LASTEXITCODE -ne 0) { throw 'ダウンロードに失敗しました。再実行してください。' }
  } else {
    $Block = 2MB; $Workers = 8; $Index = 0; $DoneBytes = [int64]0
    while (($Index * $Block) -lt $ExpectedSize) {
      $Arguments = @('--parallel', '--parallel-max', "$Workers")
      $Batch = @()
      for ($Worker = 0; $Worker -lt $Workers -and (($Index * $Block) -lt $ExpectedSize); $Worker++) {
        $Start = [int64]$Index * $Block
        $End = [Math]::Min($ExpectedSize - 1, $Start + $Block - 1)
        $Part = Join-Path $Work ('chunk-{0:D4}' -f $Index)
        if ($Batch.Count -gt 0) { $Arguments += '--next' }
        $Arguments += @('--location', '--fail', '--retry', '3', '--silent', '--show-error', '--range', "$Start-$End", $Url, '--output', $Part)
        $Batch += [pscustomobject]@{ Path = $Part; Size = ($End - $Start + 1) }
        $Index++
      }
      & $Curl @Arguments
      if ($LASTEXITCODE -ne 0) { throw '分割ダウンロードに失敗しました。再実行してください。' }
      foreach ($Part in $Batch) {
        if (-not (Test-Path -LiteralPath $Part.Path) -or (Get-Item -LiteralPath $Part.Path).Length -ne $Part.Size) { throw '受信サイズが一致しません。再実行してください。' }
        $DoneBytes += $Part.Size
      }
      $Percent = [int](100 * $DoneBytes / $ExpectedSize)
      Write-Progress -Activity 'Studioをダウンロード中' -Status ("{0:N0} / {1:N0} MB" -f ($DoneBytes / 1MB), ($ExpectedSize / 1MB)) -PercentComplete $Percent
    }
    Write-Progress -Activity 'Studioをダウンロード中' -Completed
    $OutputStream = [System.IO.File]::Open($Zip, [System.IO.FileMode]::CreateNew)
    $PartCount = $Index
    try { for ($PartIndex = 0; $PartIndex -lt $PartCount; $PartIndex++) { $PartPath = Join-Path $Work ('chunk-{0:D4}' -f $PartIndex); $PartStream = [System.IO.File]::OpenRead($PartPath); try { $PartStream.CopyTo($OutputStream) } finally { $PartStream.Dispose() } } } finally { $OutputStream.Dispose() }
    if ((Get-Item -LiteralPath $Zip).Length -ne $ExpectedSize) { throw '結合したZIPのサイズが一致しません。' }
  }
  $ActualSha256 = (Get-FileHash -LiteralPath $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($ActualSha256 -ne $ExpectedSha256) { throw 'ダウンロードの確認に失敗しました。インストールは行っていません。' }
  Write-Host '確認できました。アプリを展開しています…'
  New-Item -ItemType Directory -Force -Path $Stage | Out-Null
  Expand-Archive -LiteralPath $Zip -DestinationPath $Stage -Force
  $Exe = Join-Path $Install 'Tanutsuna Studio.exe'
  if (Test-Path -LiteralPath $Exe) {
    $Running = Get-Process -Name 'Tanutsuna Studio' -ErrorAction SilentlyContinue
    if ($Running) { throw 'Studioを終了してからインストーラーをもう一度実行してください。' }
  }
  $Backup = Join-Path $Programs ('.TanutsunaStudio-previous-' + [guid]::NewGuid().ToString('N'))
  if (Test-Path -LiteralPath $Install) { Move-Item -LiteralPath $Install -Destination $Backup }
  try {
    Move-Item -LiteralPath $Stage -Destination $Install
  } catch {
    if (Test-Path -LiteralPath $Backup) { Move-Item -LiteralPath $Backup -Destination $Install }
    throw
  }
  if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Recurse -Force }
  $ShortcutDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
  New-Item -ItemType Directory -Force -Path $ShortcutDir | Out-Null
  $Shell = New-Object -ComObject WScript.Shell
  $Shortcut = $Shell.CreateShortcut((Join-Path $ShortcutDir 'たぬつな Studio.lnk'))
  $Shortcut.TargetPath = Join-Path $Install 'Tanutsuna Studio.exe'
  $Shortcut.WorkingDirectory = $Install
  $Shortcut.IconLocation = (Join-Path $Install 'app\studio\assets\tanutsuna-studio.ico') + ',0'
  $Shortcut.Save()
  Write-Host 'インストールが完了しました。Studioを起動します。' -ForegroundColor Green
  Start-Process -FilePath (Join-Path $Install 'Tanutsuna Studio.exe')
} catch {
  Write-Host ("\nインストールできませんでした: " + $_.Exception.Message) -ForegroundColor Red
} finally {
  if (Test-Path -LiteralPath $Work) { Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue }
}
[void](Read-Host 'Enterキーで閉じる')
'''.replace('__VERSION__', VERSION).replace('__URL__', win_url).replace('__SHA256__', win_hash).replace('__SIZE__', str(windows.stat().st_size))

    readme = f'''たぬつな Studio {VERSION} かんたんインストーラー

最初のダウンロードは小さな案内ZIPです。展開後、お使いのOSのインストーラーを実行してください。
インストーラーが完全版を取得して進捗を表示し、SHA-256を確認してから配置します。
作品・素材・設定はアプリ本体とは別の場所に保存されます。

Mac
1. 「Mac/インストール.command」をcontrolキーを押しながらクリックし、「開く」を選びます。
2. macOSの確認画面でも「開く」を選びます。
3. 「続ける」を押すとダウンロードとインストールが始まります。隔離属性の処理もインストーラーが行います。

Windows 11
「Windows/インストール.bat」をダブルクリックします。Enterで開始し、終わるまでウィンドウを閉じないでください。
インストール後はスタートメニューにショートカットが追加されます。

直接ZIPを使う場合は、GitHub ReleaseからMac版またはWindows版の完全版ZIPを選んでください。
'''
    destination = ROOT / 'dist/Tanutsuna-Studio-Installer.zip'
    files = {
        'はじめに.txt': readme,
        'Mac/インストール.command': mac_script,
        'Windows/インストール.bat': '@echo off\r\nchcp 65001 >nul\r\nset "SCRIPT=%~dp0インストール.ps1"\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"\n',
        'Windows/インストール.ps1': windows_script,
    }
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in files.items():
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            mode = 0o755 if name.endswith('.command') else 0o644
            entry.external_attr = (stat.S_IFREG | mode) << 16
            data = content.encode('utf-8')
            if name.endswith('.ps1'):
                data = b'\xef\xbb\xbf' + data
            archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    print(f'{destination}: {destination.stat().st_size:,} bytes')
    return destination


if __name__ == '__main__':
    build()
