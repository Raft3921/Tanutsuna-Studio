#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h}"
cd "$ROOT"

choice="$(osascript <<'APPLESCRIPT'
set options to {"手元の変更をGitHubへアップロード", "GitHubの最新版を取得してMacアプリを作る", "GitHubの最新版を取得してWindows配布ZIPを作る", "GitHubの最新版を取得する（ビルドなし）"}
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

if [[ "$choice" == "手元の変更をGitHubへアップロード" ]]; then
  printf '\nGitHub mainの状態を確認しています…\n'
  git fetch origin main
  if ! git merge-base --is-ancestor origin/main HEAD; then
    osascript -e 'display dialog "GitHub側にこの作業フォルダにない更新があります。手元の変更は保持しました。先にGitHubの最新版を取得してから、もう一度アップロードしてください。" buttons {"OK"} with icon caution'
    exit 1
  fi

  changed="$(git status --short --untracked-files=all)"
  ahead="$(git rev-list --count origin/main..HEAD)"
  if [[ -z "$changed" ]]; then
    if (( ahead == 0 )); then
      osascript -e 'display dialog "アップロードする手元の変更はありません。" buttons {"OK"} with icon note'
      exit 0
    fi
    pending="$(git log --oneline origin/main..HEAD)"
    answer="$(osascript - "$pending" <<'APPLESCRIPT'
on run argv
  try
    display dialog "GitHubへ未送信のコミットをアップロードします。" & return & return & (item 1 of argv) buttons {"キャンセル", "アップロード"} default button "アップロード" with icon caution
    return "upload"
  on error number -128
    return "cancel"
  end try
end run
APPLESCRIPT
)"
    [[ "$answer" == "cancel" ]] && exit 0
    git push origin main || { osascript -e 'display dialog "アップロードに失敗しました。コミットは手元に保持されています。通信状態を確認して、もう一度実行してください。" buttons {"OK"} with icon stop'; exit 1; }
    osascript -e 'display dialog "手元のコミットをGitHubのmainへアップロードしました。" buttons {"OK"} with icon note'
    exit 0
  fi
  if print -r -- "$changed" | rg -i '(^|[ /])(\.env[^/]*|[^/]*(secret|credential|token|private.?key)[^/]*|[^/]*\.(pem|p12|key|mobileprovision|provisionprofile))($|/)' >/dev/null; then
    osascript -e 'display dialog "秘密情報または証明書らしきファイルが変更一覧にあります。安全のためアップロードを中止しました。対象ファイルを確認してください。" buttons {"OK"} with icon stop'
    exit 1
  fi

  commit_message="$(osascript - "$changed" <<'APPLESCRIPT'
on run argv
  set changedFiles to item 1 of argv
  try
    set answer to display dialog "次の手元の変更をまとめてGitHubのmainへアップロードします。" & return & return & changedFiles & return & return & "コミットメッセージ:" default answer "Update project from Mac" buttons {"キャンセル", "続ける"} default button "続ける" with icon caution
    return text returned of answer
  on error number -128
    return "cancel"
  end try
end run
APPLESCRIPT
)"
  [[ "$commit_message" == "cancel" ]] && exit 0
  if [[ -z "${commit_message//[[:space:]]/}" ]]; then
    osascript -e 'display dialog "コミットメッセージを入力してください。変更はアップロードしていません。" buttons {"OK"} with icon caution'
    exit 1
  fi

  git add -A -- .
  git diff --cached --check
  git commit -m "$commit_message"
  git push origin main || { osascript -e 'display dialog "アップロードに失敗しました。コミットは手元に保持されています。通信状態を確認して、もう一度実行してください。" buttons {"OK"} with icon stop'; exit 1; }
  osascript -e 'display dialog "手元の変更をGitHubのmainへアップロードしました。" buttons {"OK"} with icon note'
  exit 0
fi

if [[ -n "$(git status --short --untracked-files=all)" ]]; then
  osascript -e 'display dialog "手元に未コミットの変更があります。先に「手元の変更をGitHubへアップロード」を実行してください。ファイルは変更していません。" buttons {"OK"} with icon caution'
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
