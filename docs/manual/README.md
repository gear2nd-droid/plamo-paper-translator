# 論文形式のユーザーマニュアル（manual.pdf）

[`manual.pdf`](manual.pdf) は、PLaMo Paper Translator の使い方を英語の学術論文の体裁で書いたマニュアルです。デモと動作確認に使うサンプル論文を兼ねています。

## ライセンス：CC0 1.0（パブリックドメイン）

この文書（`manual.tex`、`manual.pdf`、`figures/` の画像）は [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/deed.ja) によりパブリックドメインに提供します。翻訳、動画やスクリーンショットへの掲載、再配布、改変は、目的を問わず許可も表示もなしで行えます。

- スクリーンショットに映る論文は、このマニュアル自身です。日本語訳は手書きしたもので、第三者の論文は含みません。
- 本アプリでこの文書を翻訳して公開する場合、訳文はPLaMoの生成物になります。そのため、PLaMo Community Licenseに従って `Built with PLaMo` の表示が必要です。エクスポートしたファイルには自動で付きます。

## 収録内容（動作確認の観点）

| 要素 | 数 | 内容 |
|---|---:|---|
| 章・節 | 11章＋付録2 | 番号付きの節（`3.1` など）、謝辞、参考文献、付録（`A`、`B`） |
| 図 | 8 | 二段にまたがる図、段内の図、(a)(b) のサブ図、縦長の画面写真 |
| 表 | 8 | booktabs形式の表（`\verb` を含む表も）|
| 番号付き数式 | 15 | 分数、総和、集合、場合分け（`cases`）、`align` による複数行の式 |
| その他 | — | アルゴリズム1件、番号付き・番号なしの箇条書き、参考文献10件、本文中の図・式・表への参照、本文中の下付き・上付き文字 |

## 再生成の手順

リポジトリのルートで実行します。

```powershell
# 1. 構成図・グラフを生成
.venv\Scripts\python.exe docs\manual\make_figures.py
# 2. 画面のスクリーンショットを撮影（Chromeが必要。一時データを使い、ライブラリは変更しない。
#    まとめパネルを開くため、撮影中だけ static/_manual_capture.html を置いて終了時に消す）
.venv\Scripts\python.exe docs\manual\capture_screens.py
# 3. PDFを組版（MiKTeXなどのpdflatex。相互参照のため2回実行）
cd docs\manual
pdflatex manual.tex
pdflatex manual.tex
```
