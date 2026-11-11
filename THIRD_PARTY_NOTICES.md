# Third-party notices

この配布物は外部ソフトウェアを利用し、モデルと推論ランタイムを初回セットアップ時に公式配布元から取得します。各コンポーネントにはそれぞれのライセンスが適用されます。ポータブルZIPには `licenses/` 以下に同梱コンポーネントのライセンス本文を収録します。

## PLaMo 3 Translate

- Copyright: Preferred Networks, Inc. and applicable contributors
- License: PLaMo Community License
- Japanese license: https://plamo.preferredai.jp/info/plamo-community-license-ja
- Model repository: https://huggingface.co/pfnet/plamo-3-translate-8b-GGUF

モデルはこのアプリのポータブルZIPには同梱しません。利用者がライセンスを確認したうえで、公式モデルリポジトリから取得します。

本アプリのUIとエクスポートには `Built with PLaMo` および生成元モデルを表示します。生成物を第三者へ提供・公表する場合は、PLaMo Community License第4条・第5条を確認してください。商業目的には組織内部での利用も含まれ、所定条件下では事前登録が必要です。

## llama.cpp

- Project: https://github.com/ggml-org/llama.cpp
- License: MIT

## pypdf

- Project: https://github.com/py-pdf/pypdf
- License: BSD-3-Clause
- Included license: `licenses/pypdf-LICENSE.txt`

## Pillow

- Project: https://python-pillow.org/
- License: MIT-CMU
- Included license: `licenses/Pillow-LICENSE.txt`

## pypdfium2 / PDFium

- Project: https://github.com/pypdfium2-team/pypdfium2
- License: Apache-2.0 OR BSD-3-Clause; PDFium and bundled dependencies use the licenses shipped with the wheel
- Included licenses: `licenses/pypdfium2/`

PDFiumは独立表示数式をPDFから画像として切り出すために使用します。バイナリ配布時はwheelに収録された `BUILD_LICENSES/` を含むライセンス一式を同梱します。

## KaTeX

- Project: https://katex.org/ （同梱バージョン 0.16.11）
- License: MIT
- Included license: `licenses/KaTeX-LICENSE.txt`（ソースでは `static/vendor/katex/LICENSE`）

論文のまとめに書いた `$…$` の数式を表示するために使用します。外部サーバーから読み込まず、`static/vendor/katex/` の同梱ファイル（JavaScript、CSS、フォント）だけを使います。

## Python

- Project: https://www.python.org/
- License: Python Software Foundation License Version 2 and bundled notices
- Included license: `licenses/Python-LICENSE.txt`

## PyInstaller

- Project: https://pyinstaller.org/
- License: GPL-2.0-or-later with Bootloader Exception; selected files are Apache-2.0
- Included license: `licenses/PyInstaller-COPYING.txt`

PyInstallerの公式例外により、生成した実行ファイルは依存物のライセンスに従って配布できます。

このファイルは要約です。正確な条件は、取得時点の各公式ライセンス本文を優先してください。
