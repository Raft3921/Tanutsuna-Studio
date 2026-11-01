# たぬつな Studio 1.4.8

## Windowsカメラ追跡

- Windows配布時にQt WebEngineの`v8_context_snapshot.bin`が除外されていた問題を修正しました。追跡画面のページ自体が読み込めなくなる原因でした。
- Windows版起動時に同梱のWebEngineプロセス、リソース、ロケールの場所を明示します。
- 追跡画面の読み込みに失敗した時は、WebEngineのエラーコードと内容を表示します。

## たぬきのグラサン

- GPU描画と互換描画の両方に鏡面反射を追加し、レンズに明るい反射が入るようにしました。

## 対応環境

- macOS Apple silicon
- Windows 11 x64

Windows実機のカメラ・マイク許可および配信動作は、このビルド環境では未確認です。
