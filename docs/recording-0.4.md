# 独自収録 0.4 / 2026-10-28

## 今回の確認

- Mac実画面の2秒収録で、1920×1080・50.0fpsのH.264 MP4、PC音とマイクの独立MP3を生成。サンプル時刻を記録し、映像の開始・終了に音声をトリム・無音補完。ネイティブ側の処理待ちカウンターは0。
- 既知の日本語音声から、同梱するtiny-q5_1多言語モデルで日本語を文字起こしし、TXTとSRTを出力。
- 動きログから720×720・50fpsのV体MP4を別プロセスで生成。
- 合成テストの1080p・50fps素材から別MP3を作成し、実際のCut RaftへV1・A1・A2として同期配置・保存。2回読み込んでもカット数が増えないことを確認。
- AquesTalkPlayerの引数とShift-JISファイル生成・結果ファイルへの置換をモックで確認。Mac版AquesTalkPlayerの実コマンド連携でも、日本語の文字起こしからWAVを生成できることを確認。Windows実機での音声生成は未確認。

短い収録の確認であり、ゲーム同時実行、低スペックWindows、長時間収録、音ズレの実聴、ディスク逼迫時の確認は未実施。Windows WASAPIヘルパーはソースを実装済みですが、このMacでは実行できないためWindows実機でのコンパイル・録音は未検証です。

## 負荷を分ける理由

収録中は画面のハードウェアH.264と各入力の保存を優先。MP3への変換、Whisper認識、V体のオフライン映像化は収録後に回します。先に映像と音声を編集に渡し、V体が完成したら同じ開始位置へ追加します。カメラ追跡は既存のローカルブラウザで動作するため、その分のCPU負荷は残ります。

Macの静止画面ではScreenCaptureKitが毎秒50枚の新しい画面を返すとは限らないため、同じGPUサーフェスを必要な間隔で再利用し、50fpsの動画タイムラインへ書き出します。CPUで画面全体を毎回読み戻しません。ネイティブ出力のキュー深度は3です。

WindowsのPC音はWASAPI共有モードのループバック。マイクとは混ぜません。長いPC音録音はRF64で4GBを越えるサイズにも備えます。WindowsのQt画面フレームと各音声には共通のサンプルクロックが公開されていないため、最初のバッファ到着時刻から相対オフセットを推定します。サンプル精度の同期とはしていません。

## 参照

- [Qt画面収録](https://doc.qt.io/qt-6/qscreencapture.html)
- [Qt FFmpegハードウェア構成](https://doc.qt.io/qt-6/advanced-ffmpeg-configuration.html)
- [Microsoft WASAPIループバック](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)
- [AquesTalkPlayer公式コマンド仕様](https://www.a-quest.com/products/aquestalkplayer_man.html)
- [Whisper.cpp](https://github.com/ggml-org/whisper.cpp)

文字起こしの配布モデルとソースの取得先・SHA-256は `studio/assets/speech/source-manifest.json`、ライセンスは同じフォルダの `WHISPER-LICENSE.txt` に保存します。
