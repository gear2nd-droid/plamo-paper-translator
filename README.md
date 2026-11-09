# PLaMo Paper Translator

PLaMo 3 Translate をこのPC上で動かし、PDF論文を一文単位の対訳にするローカルアプリです。原文・訳文・箇所別メモを3ペインで確認でき、小節構造から生成した目次、セグメント別ブックマーク、固定クリップを使いながら読み進められます。結果はHTML / Markdown / JSON へ保存できます。

> Built with PLaMo. モデル本体はリポジトリに同梱せず、初回セットアップ時にPFNの公式配布元から取得します。

## このPC向けの構成

- GPU: NVIDIA RTX 2000 Ada Generation Laptop GPU、VRAM 16GB
- 推奨モデル: `pfnet/plamo-3-translate-8b-GGUF` の `Q4_K_M`（5.28GB）
- 推論: llama.cpp Windows CUDA版
- 文脈長: 16,384トークン
- UI: `127.0.0.1` のみに公開するローカルWeb UI

31B Q4_K_M は約19.53GBでVRAM容量を超えるため既定にはしていません。8Bで重い場合は `setup.ps1 -ModelSize 2b` を使うと、1.81GBの2Bモデルに切り替えられます。

## 最初の起動

1. `Start-PLaMoPaperTranslator.cmd` をダブルクリックします。
2. 初回はライセンスの要点と公式URLが表示されます。内容を確認して `yes` と入力します。
3. llama.cpp、PLaMo 3 Translate 8B、PDF処理ライブラリが取得されます（合計約6GB）。
4. セットアップ後、もう一度 `Start-PLaMoPaperTranslator.cmd` を開きます。
5. ブラウザで `http://127.0.0.1:7865` が開きます。

PowerShellから明示的に実行する場合:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1 -ModelSize 8b
.\start.ps1
```

## Chromeからワンクリックで起動

ポータブル版にはManifest V3のChromeランチャーを同梱しています。初回だけ次の設定を行うと、拡張アイコンまたは `Alt + Shift + P` で、停止中のアプリをバックグラウンド起動して画面を開けます。既に起動中なら重複起動せず、既存タブを前面へ移します。

1. Chromeで `chrome://extensions` を開き、「デベロッパー モード」を有効にします。
2. 「パッケージ化されていない拡張機能を読み込む」を押し、配布フォルダー内の `chrome-extension` を選びます。
3. `chrome-extension\install-native-host.ps1` を右クリックして「PowerShellで実行」します。
4. アドレスバーで `chrome://restart` を開き、Chromeを再起動します。
5. `PLaMo Paper Translator Launcher` をツールバーへピン留めします。

通常の登録に管理者権限は不要です。`Specified native messaging host not found.` が続く場合のPC全体登録、移動・更新後の再登録、解除方法は [`chrome-extension/README.md`](chrome-extension/README.md) を参照してください。エラー画面は自動更新されないため、登録後は古いエラータブを閉じて拡張アイコンをもう一度押します。

## モデルの取得

### 推奨: セットアップスクリプトで取得

PowerShellをプロジェクトフォルダで開き、次を実行します。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1 -ModelSize 8b
```

スクリプトは次の処理を行います。

1. PLaMo Community Licenseと公式モデルURLを表示し、同意を確認します。
2. `.venv/` を作り、`pypdf`、図処理用の `Pillow`、数式画像化用の `pypdfium2 / PDFium` をインストールします。
3. NVIDIA GPUがあればllama.cppのWindows CUDA 12.4版、なければVulkan版を公式GitHub Releasesから取得します。
4. PFN公式Hugging Faceからモデルとチャットテンプレートを取得します。
5. GGUFヘッダー、ファイルサイズ、SHA-256をHugging Face公式APIの現在値と照合します。

既定の8Bモデル:

| 項目 | 値 |
|---|---|
| リポジトリ | `pfnet/plamo-3-translate-8b-GGUF` |
| ファイル | `plamo-3-translate-8b-Q4_K_M.gguf` |
| サイズ | 5,280,445,728 bytes（約5.28GB、2026-11-08時点） |
| SHA-256 | `94e5d3f91b421f7c8900ddaac27b11a19f7441426c954bbb25a2fd60a1640187`（同日時点） |
| 保存先 | `models/plamo-3-translate-8b-Q4_K_M.gguf` |
| 追加ファイル | `models/chat_template.jinja` |

途中で通信が切れた場合は、同じコマンドをもう一度実行してください。`.partial` ファイルから再開します。完成済みファイルは再取得しません。

### 軽量な2Bモデルを使う

VRAMが少ないPCや速度を優先する場合:

```powershell
.\setup.ps1 -ModelSize 2b
```

取得するファイルは `plamo-3-translate-2b-Q4_K_M.gguf`（1,807,203,040 bytes、約1.81GB、2026-11-08時点）です。8Bと2Bを両方置いた場合、アプリはファイル名順で最初に見つかったGGUFを使うため、使用しない方を `models/` の外へ移してください。

### モデルだけ手動で取得

llama.cppとPython環境を既に準備済みの場合は、公式URLからモデルだけ取得できます。

```powershell
New-Item -ItemType Directory -Force .\models | Out-Null

curl.exe -L --fail --retry 4 -C - `
  -o .\models\plamo-3-translate-8b-Q4_K_M.gguf.partial `
  "https://huggingface.co/pfnet/plamo-3-translate-8b-GGUF/resolve/main/plamo-3-translate-8b-Q4_K_M.gguf?download=true"

if ($LASTEXITCODE -ne 0) {
  throw "モデルのダウンロードに失敗しました。同じコマンドで再開してください。"
}

Move-Item `
  .\models\plamo-3-translate-8b-Q4_K_M.gguf.partial `
  .\models\plamo-3-translate-8b-Q4_K_M.gguf

curl.exe -L --fail --retry 4 `
  -o .\models\chat_template.jinja `
  "https://huggingface.co/pfnet/plamo-3-translate-8b-GGUF/resolve/main/chat_template.jinja?download=true"
```

取得後の確認:

```powershell
Get-Item .\models\*.gguf | Select-Object Name, Length
Get-ChildItem .\runtime -Filter llama-server.exe -Recurse
nvidia-smi
```

### モデル取得時の注意

- 空き容量は8B構成で最低10GB、ビルド成果物も作る場合は15GB以上を推奨します。
- モデルはGit管理対象外です。`.gitignore` により `.gguf`、ランタイム、論文、翻訳結果はpushされません。
- プロキシ環境では `HTTPS_PROXY` を設定してから `setup.ps1` を再実行してください。
- 組織内利用はPLaMo Community License上の商業目的に含まれます。該当する場合はPFNの条件に従って事前登録または契約を行ってください。
- 取得元を変更した非公式GGUFは、テンプレートや量子化方式が異なる可能性があるためサポート対象外です。

## 使い方

1. PDF / TXT / Markdown をドロップします。
   ファイル名または冒頭本文にarXiv ID／DOIがあれば、「原文URL / DOI」へ対応URLを自動入力します。検出はローカルで行い、外部通信はしません。
2. 分割モード（`一文単位` または `段落単位`）、原文と翻訳先の言語、日本語の文体を選びます。
3. 必要なら参考文脈と用語集を入力します。用語集は `原語 = 訳語` を1行ずつ書きます。
4. 「翻訳を開始」を押します。
5. 完了後、左の原文、中央の訳文、右のメモを選択した単位で確認します。一文単位では各行の `P段落番号 / S文番号` と太い区切りで段落境界も確認できます。左側の目次から節・小節へ移動でき、その下の「図表・数式」一覧または英語・日本語の図・式・表の参照リンクから対応セグメントへ移動できます。移動後は固定上部バーの「戻る」で参照元へ戻れます。各メモ欄の☆でブックマーク、`E 英語`・`J 日本語`・`M メモ`で選んだ内容だけを最大3件まで画面右下へ固定できます。メモ・ブックマーク・クリップは自動保存されます。
   訳文上部の「編集」からそのセグメントだけを校正でき、「再翻訳」は同じ段落の前後文・参考文脈・用語集を再利用して1件だけモデルへ送り直します。変更前の訳文は最大20版まで保持され、「履歴」から確認・復元できます。画像化された数式は原文画像を正本とするため編集・再翻訳の対象外です。
6. 固定上部バーから対訳の検索とHTML / Markdown / JSON保存を行えます。「まとめ」を押すと、論文全体のタグ（最大20件）と概要を記録できます。「研究ノート雛形」は研究課題・手法・実験条件・結果・新規性・限界・次に確認することの欄を挿入します。タグと概要は翻訳ジョブに保存され、再読み込み後も復元されます。JSON出力にも含まれます。HTMLは図とメモを埋め込んだ単体ファイルで、各形式に `Built with PLaMo` と生成元モデルを記録します。
   「翻訳検査」は未翻訳、原文と同一の訳、欠落した数値・単位、極端に短い訳、用語集の訳語漏れを機械的に確認し、結果から該当セグメントへ移動できます。これは投稿前の人手校正を置き換えるものではありません。

上部の「ライブラリ」では、保存済みの論文を一覧表示し、前回読んでいたセグメントから再開できます。タイトル・タグ・概要・原文・訳文・メモを横断検索でき、未読・読書中・読了・保留で絞り込めます。同じPDFをもう一度読み込んだ場合は既存翻訳へのリンクを表示します。「ZIPバックアップ」はジョブ、元ファイル、抽出画像をまとめて保存し、「ZIPから復元」は既存ファイルを上書きせず新規データだけを取り込みます。ライブラリの保存先は画面内にも表示され、通常版はプロジェクトの `data/`、ポータブル版はランチャー設定で指定されたアプリホームの `data/` です。`Alt + L` でライブラリ、`Alt + S` で現在の論文のまとめを開けます。

公開動画やWeb記事に使いやすいCC BY 4.0論文の候補と、投稿時のクレジット例は [`docs/PUBLIC_DEMO_PAPERS.md`](docs/PUBLIC_DEMO_PAPERS.md) にまとめています。

一文単位モードは論文を一文ずつ分割し、翻訳時に同じ段落の前後文を参考文脈として付与します。段落単位モードは同じ段落の文をまとめてモデルへ渡します。どちらも見出し、数式、表、図キャプション、箇条書きを構造ブロックとして保持します。番号付きの独立数式はPDFの該当領域を高解像度画像として表示し、抽出テキストも検索・コピー用に折りたたんで保持します。図はPDFページ上の実表示領域をレンダリングして切り出すため、埋め込み画像とは別に重ねられた `(a)(b)(c)`、数式記号、注釈も保持します。`REFERENCES` 以降は著者・年・題名・掲載誌を1件の引用単位にまとめ、各ページで反復するヘッダーとフッターは本文から除外します。PLaMo公式の `chat_template_kwargs` を使って `target_lang`、`source_lang`、`context`、`glossary`、`form` をモデルへ渡します。

閲覧画面はA4や元PDFのページ境界に合わせず、PCとタブレットの横幅を使う研究リーダー形式です。PCでは階層目次を本文の左側、ブックマーク一覧を右側に固定し、現在読んでいる節を目次上で追跡します。`Fig./Figure/図`、`Eq./Equation/式`、`Table/表` の番号付き参照は、同じ番号の候補のうち実画像が付いたセグメントを優先してリンクします。図番号と画像が別行に分かれたPDFでは、同じページ・段落付近の画像付き行を図番号へ関連付けます。図・数式が本文で初めて参照された時点より後ろにある場合は、閲覧画面上で画像付きセグメントを初回参照の直後へ配置します。参照ジャンプは最大20回分の戻り履歴を保持します。クリップした英語・日本語・メモは、選択した内容だけを画面右下に常時表示します。図・数式の英語または日本語をクリップした場合は対応する画像も一緒に表示します。タブレットでは目次とブックマーク一覧が本文上部へ移り、さらに狭い画面では原文・訳文の下へメモ欄が回り込みます。クリップ状態は保存ジョブから再読み込み時に復元され、JSON出力にも含まれます。

本文・参考文献内の `https://...`、DOI、arXiv IDは外部リンクとして表示されます。クリックすると原文情報を新しいタブで開きます。

## 対応範囲と制約

- テキストを内包するPDFに対応します。画像だけのスキャンPDFは、先にOCRしてください。
- 二段組みはPDF内部の読み順に沿って抽出します。複雑な脚注や数式の文字表現はPDFの内部構造に依存します。
- PDFに埋め込まれたラスター図は抽出して図キャプション付近へ配置します。ベクターだけで描かれた図や、画像化されていない表は原文テキストとして保持されます。
- 番号付きの独立数式は216dpi相当のPNGとして切り出します。インライン数式と画像化できない数式は抽出テキストへフォールバックします。
- 翻訳は機械生成です。投稿・契約・医療判断などに使う場合は、専門家による校正が必要です。
- 初回ダウンロード後の翻訳はオフラインで動作します。
- 論文と結果は `data/`、モデルは `models/`、推論ランタイムは `runtime/` に保存されます。

## データを消す

アプリを終了してから `data/uploads/` と `data/jobs/` の中身を削除してください。モデルも削除する場合は `models/` の `.gguf` を削除します。再度 `setup.ps1` を実行すれば取得できます。

## 配布物を作る

開発PCで次を実行すると、Pythonを同梱したモデル非同梱のポータブルZIPが `dist/` に作られます。

```powershell
.\build-portable.ps1
```

モデルはPLaMo Community Licenseの確認と公式配布元からの取得を受取人ごとに行う設計です。ポータブルZIPにはアプリのMIT Licenseと、同梱するPython・pypdf・Pillow・pypdfium2/PDFium・PyInstallerのライセンス本文を収録します。詳しい配布方式の比較は `DISTRIBUTION.md` を参照してください。

## ライセンス

アプリ独自コードはMIT Licenseです。外部コンポーネントにはそれぞれのライセンスが適用されます。

- PLaMo Community License（日本語）: https://plamo.preferredai.jp/info/plamo-community-license-ja
- PLaMo 3 Translate 8B GGUF: https://huggingface.co/pfnet/plamo-3-translate-8b-GGUF
- llama.cpp: https://github.com/ggml-org/llama.cpp
- pypdf: https://pypdf.readthedocs.io/
- Pillow: https://python-pillow.org/

生成物を第三者へ提供・公表する場合は、`Built with PLaMo`、生成元の明示、ライセンスの提供など、PLaMo Community License第4条・第5条の条件を確認してください。組織内利用を含む商業目的では、直近事業年度の収入または売上が10億円以下でも事前登録が必要で、超える場合はPFNから別途商業用ライセンスを取得する必要があります。
