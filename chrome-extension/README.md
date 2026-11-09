# Chrome Launcher

Chromeの拡張アイコンまたは `Alt + Shift + P` からPLaMo Paper Translatorを開くManifest V3拡張です。アプリが停止中ならバックグラウンドで起動し、起動済みなら既存タブへ移動します。

## 初回設定

1. Chromeで `chrome://extensions` を開き、「デベロッパー モード」を有効にします。
2. 「パッケージ化されていない拡張機能を読み込む」で、この `chrome-extension` フォルダーを選択します。
3. `install-native-host.ps1` を右クリックし、「PowerShellで実行」します。
4. Chromeで `chrome://restart` を開きます。
5. `PLaMo Paper Translator Launcher` をツールバーへピン留めし、アイコンを押します。

拡張機能IDは固定公開鍵により `cpiiadekehcofkocnlplcaolgioefcjd` になります。通常の登録先は現在のWindowsユーザーの `HKCU` で、管理者権限は不要です。

## 管理者権限でのPC全体登録

通常登録後も `Specified native messaging host not found.` と表示される場合は、`install-native-host-all-users.ps1` を実行してUACを許可します。このスクリプトはPC全体の `HKLM` と、HKLMより優先される現在ユーザーの `HKCU` を、同じProgramData内のmanifestへ登録します。

登録後は古いエラータブを閉じ、拡張アイコンをもう一度押してください。エラー画面自体は自動更新されません。

## フォルダーを移動・更新した場合

Native Messagingのmanifestには `PLaMoPaperTranslator.exe` の絶対パスが記録されます。配布フォルダーを移動した場合や、新しいポータブル版へ入れ替えた場合は、移動先にある登録スクリプトをもう一度実行してください。

登録スクリプトは実行ファイルとアプリホームも記録します。そのため開発用ビルドから起動した場合でも、プロジェクト直下の `models/`、`runtime/`、`data/` を利用できます。モデルの読み込みはアプリサーバー起動後、llama.cpp側で行われます。

## トラブルシューティング

| 症状 | 対応 |
|---|---|
| `Specified native messaging host not found.` | 通常登録をやり直し、改善しなければPC全体登録を行って `chrome://restart` を開きます。古いエラータブではなく、拡張アイコンから再試行します。 |
| 拡張機能IDが異なる | このフォルダーを読み込み直し、IDが `cpiiadekehcofkocnlplcaolgioefcjd` であることを確認します。 |
| フォルダー移動後に起動しない | 移動先の `install-native-host.ps1`、またはPC全体登録用スクリプトを再実行します。 |
| 起動済みなのに新しい画面が増える | `http://127.0.0.1:7865/` の既存タブを確認します。拡張は同じURLのタブを優先して前面へ移動します。 |

## 解除

- 通常登録: `uninstall-native-host.ps1`
- PC全体登録: `uninstall-native-host-all-users.ps1`（UACあり）

登録解除後、`chrome://extensions` で拡張機能を削除します。

## セキュリティとライセンス

Native Messagingホストを呼び出せるのは、固定IDの本拡張だけです。アプリのWeb UIは `127.0.0.1` のみに公開され、拡張から外部サービスへ論文や翻訳結果を送信しません。

この拡張のソースコードには、リポジトリルートのMIT Licenseが適用されます。PLaMoモデル本体は同梱せず、利用者がPLaMo Community Licenseを確認したうえで公式配布元から取得します。
