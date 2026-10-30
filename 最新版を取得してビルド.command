#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h}"
cd "$ROOT"

choice="$(osascript <<'APPLESCRIPT'
set options to {"GitHubの最新版を取得してMacアプリを作る", "GitHubの最新版を取得してWindows配布ZIPを作る", "GitHubの最新版を取得する（ビルドなし）"}
set selected to choose from list options with title "たぬつな Studio" with prompt "実行する作業を選んでください。"
if selected is false then return "cancel"
return item 1 of selected
APPLESCRIPT
)"
[[ "$choice" == "cancel" ]] && exit 0

if [[ "$(git branch --show-current)" != "main" ]]; then
  osascript -e 'display dialog "mainブランチに切り替えてから実行してください。作業中のブランチは変更していません。" buttons {"OK"} with icon caution'
  exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  osascript -e 'display dialog "未保存のソース変更があります。先にGitHubへ保存するか、変更を退避してください。ファイルは変更していません。" buttons {"OK"} with icon caution'
  exit 1
fi

printf '\nGitHub mainから最新版を取得しています…\n'
git fetch origin main
git pull --ff-only origin main

case "$choice" in
  "GitHubの最新版を取得してMacアプリを作る")
    CUTRAFT_SOURCE="${CUTRAFT_SOURCE:-$HOME/Documents/Cut Raft}"
    PYTHON="$CUTRAFT_SOURCE/.venv/bin/python"
    if [[ ! -x "$PYTHON" ]]; then
      osascript -e 'display dialog "Cut RaftのPython環境が見つかりません。CUTRAFT_SOURCEを確認してください。" buttons {"OK"} with icon stop'
      exit 1
    fi
    if [[ ! -f "$ROOT/vendor/cutraft-win/app/cutraft/app.py" ]]; then
      printf '\n配布元の素材を準備しています…\n'
      CUTRAFT_SOURCE="$CUTRAFT_SOURCE" "$PYTHON" scripts/build_native.py --prepare-only
    fi
    printf '\nMacアプリを作成しています…\n'
    "$PYTHON" scripts/build_mac.py
    VERSION="$(PYTHONPATH="$ROOT" "$PYTHON" -c 'from studio.version import STUDIO_VERSION; print(STUDIO_VERSION)')"
    ditto -c -k --sequesterRsrc --keepParent "$ROOT/dist/Tanutsuna Studio.app" "$ROOT/dist/Tanutsuna-Studio-macOS-arm64.zip"
    open -R "$ROOT/dist/Tanutsuna-Studio-macOS-arm64.zip"
    printf '\nMac版 %s を作成しました。表示されたZIPが配布ファイルです。\n' "$VERSION"
    ;;
  "GitHubの最新版を取得してWindows配布ZIPを作る")
    CUTRAFT_SOURCE="${CUTRAFT_SOURCE:-$HOME/Documents/Cut Raft}"
    printf '\nWindows配布ZIPを作成しています…\n'
    CUTRAFT_SOURCE="$CUTRAFT_SOURCE" python3 scripts/build_native.py
    open -R "$ROOT/dist/Tanutsuna-Studio-Windows11-x64.zip"
    printf '\nWindows配布ZIPを作成しました。表示されたZIPが配布ファイルです。\n'
    ;;
  *)
    open "$ROOT"
    printf '\nGitHub mainの最新版を取得しました。\n'
    ;;
esac

osascript -e 'display notification "選んだ作業が完了しました。" with title "たぬつな Studio"'
