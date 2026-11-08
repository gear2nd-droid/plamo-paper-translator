# PLaMo Paper Translator

PLaMo 3 Translate をこのPC上で動かし、PDF論文を一文単位の対訳にするローカルアプリです。原文・訳文・箇所別メモを3ペインで確認でき、HTML / Markdown / JSON へ保存できます。

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

## モデルの取得

### 推奨: セットアップスクリプトで取得

PowerShellをプロジェクトフォルダで開き、次を実行します。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1 -ModelSize 8b
```

スクリプトは次の処理を行います。

1. PLaMo Community Licenseと公式モデルURLを表示し、同意を確認します。
2. `.venv/` を作り、`pypdf` と図抽出用の `Pillow` をインストールします。
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
2. 原文と翻訳先の言語、日本語の文体を選びます。
3. 必要なら参考文脈と用語集を入力します。用語集は `原語 = 訳語` を1行ずつ書きます。
4. 「翻訳を開始」を押します。
5. 完了後、左の原文、中央の訳文、右のメモを一文ずつ確認します。各行の `P段落番号 / S文番号` と太い区切りで段落境界も確認でき、メモは入力後に自動保存されます。
6. 対訳をHTML / Markdown / JSONで保存します。HTMLは図とメモを埋め込んだ単体ファイルです。

アプリは論文を一文単位へ分割し、見出し、数式、表、図キャプションを別ブロックとして保持します。表示とメモは一文単位ですが、翻訳時は同じ段落の前後文を参考文脈として付与します。PLaMo公式の `chat_template_kwargs` を使って `target_lang`、`source_lang`、`context`、`glossary`、`form` をモデルへ渡します。

閲覧画面はA4や元PDFのページ境界に合わせず、PCとタブレットの横幅を使う研究リーダー形式です。タブレットの狭い画面では、原文・訳文の下へメモ欄が回り込みます。

## 対応範囲と制約

- テキストを内包するPDFに対応します。画像だけのスキャンPDFは、先にOCRしてください。
- 二段組みはPDF内部の読み順に沿って抽出します。複雑な脚注や数式の文字表現はPDFの内部構造に依存します。
- PDFに埋め込まれたラスター図は抽出して図キャプション付近へ配置します。ベクターだけで描かれた図や、画像化されていない表は原文テキストとして保持されます。
- 数式と数値中心の表は原文を崩さないよう等幅表示します。意味を変えないため、非言語的な数式は翻訳せず原文を併記します。
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

モデルはPLaMo Community Licenseの確認と公式配布元からの取得を受取人ごとに行う設計です。詳しい配布方式の比較は `DISTRIBUTION.md` を参照してください。

## ライセンス

アプリ独自コードはMIT Licenseです。外部コンポーネントにはそれぞれのライセンスが適用されます。

- PLaMo Community License（日本語）: https://plamo.preferredai.jp/info/plamo-community-license-ja
- PLaMo 3 Translate 8B GGUF: https://huggingface.co/pfnet/plamo-3-translate-8b-GGUF
- llama.cpp: https://github.com/ggml-org/llama.cpp
- pypdf: https://pypdf.readthedocs.io/
- Pillow: https://python-pillow.org/

生成物を公表する場合の表示義務や、組織内利用を含む商用利用の登録条件について、必ず最新のPLaMo Community License本文を確認してください。
