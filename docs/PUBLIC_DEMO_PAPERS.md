# 公開デモに使いやすい論文候補

動画、スクリーンショット、Web記事で本文・図・翻訳結果を見せる場合は、論文ごとのライセンス確認が必要です。ここでは、arXivの論文ページから **CC BY 4.0** へのライセンスリンクを確認でき、PLaMo Paper Translatorの表示確認にも向く候補を挙げます（2026-11-09確認）。

CC BY 4.0は、適切なクレジット、ライセンスへのリンク、変更の明示を条件に、共有と翻案を認めています。論文に第三者著作物の個別表示がある場合、その部分は別条件の可能性があります。

## 最優先: 同梱の論文形式マニュアル（CC0）

[`docs/manual/manual.pdf`](manual/manual.pdf) は、このアプリのマニュアルを英語の論文形式で書いたもので、CC0（パブリックドメイン）です。原文側にはクレジット表示もライセンス確認も不要なので、動画や記事のデモに最も安全に使えます。図7点、表7点、番号付き数式14本、アルゴリズム、参考文献、付録を含みます。翻訳結果を公開する場合は、PLaMoの生成物としての表示（`Built with PLaMo`）だけが必要です。詳細は [`docs/manual/README.md`](manual/README.md) を参照してください。

## 第一候補: 画面デモ向け

### GAM Coach: Towards Interactive and User-centered Algorithmic Recourse

- arXiv: https://arxiv.org/abs/2302.14165
- PDF: https://arxiv.org/pdf/2302.14165
- 著者: Zijie J. Wang, Jennifer Wortman Vaughan, Rich Caruana, Duen Horng Chau
- 内容: 機械学習・説明可能AI・インタラクティブ可視化
- 構成: 20ページ、12図。大きな図、複数パネル図、表、数式、二段組みがあり、図の切り出しと参照ジャンプのデモに向きます。
- ライセンス: arXivページの `view license` とPDF本文で CC BY 4.0 を確認できます。

公開デモの第一候補です。論文の題材がアプリの研究読解UIと相性よく、図が多いため、対訳・目次・クリップ・図参照を短い動画で見せられます。

## 第二候補: 数式・表・図の確認向け

### Constraint on the total width of the Higgs boson … with the ATLAS detector

- arXiv: https://arxiv.org/abs/2407.10631
- PDF: https://arxiv.org/pdf/2407.10631
- 著者: ATLAS Collaboration
- 内容: 高エネルギー物理
- 構成: 31ページ、3図、2表。数式記号、表、図、長い著者一覧を含みます。
- ライセンス: arXivページの `view license` とPDF本文で CC BY 4.0 を確認できます。

数式画像化、単位、記号、長い参考文献の確認に向きます。著者一覧が長いため、翻訳時間の短いデモでは冒頭から結果節までに範囲を絞ると扱いやすくなります。

### Exclusive four pion photoproduction … (ALICE Collaboration)

- arXiv: https://arxiv.org/abs/2404.07542
- PDF: https://arxiv.org/pdf/2404.07542
- 著者: ALICE Collaboration
- 内容: 原子核実験・高エネルギー物理
- 構成: 24ページ、3図、3表。数式、誤差表記、図表参照を含みます。
- ライセンス: arXivページの `view license` とPDF本文で CC BY 4.0 を確認できます。

ATLAS論文より表が多く、図・式・表の参照リンクを一度に確認しやすい候補です。

## 第三候補: AI分野の長文読解向け

### Large Language Models and Knowledge Graphs: Opportunities and Challenges

- arXiv: https://arxiv.org/abs/2308.06374
- PDF: https://arxiv.org/pdf/2308.06374
- 著者: Jeff Z. Panほか
- 内容: LLMと知識グラフ
- 構成: 30ページ。節・小節と参考文献が多く、目次、検索、ライブラリの概要・タグ機能のデモに向きます。
- ライセンス: arXivページの `view license` とPDF本文で CC BY 4.0 を確認できます。

## 投稿時のクレジット例

動画説明欄、記事末尾、またはデモ画面の近くに、少なくとも次を記載します。

```text
Paper: “GAM Coach: Towards Interactive and User-centered Algorithmic Recourse”
Authors: Zijie J. Wang, Jennifer Wortman Vaughan, Rich Caruana, Duen Horng Chau
Source: https://arxiv.org/abs/2302.14165
License: CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/
Japanese translation and screen layout were generated/modified for this demo.
Machine translation: Built with PLaMo (PLaMo 3 Translate)
https://plamo.preferredai.jp/info/plamo-community-license-ja
```

日本語だけで記載しても構いません。翻訳は原著の改変に当たるため、「日本語機械翻訳を追加」「画面用にレイアウトを変更」などの変更表示を含めます。著者・arXiv URL・CC BY 4.0へのリンクを残し、著者が翻訳やアプリを推奨しているような表現は避けます。

## 公開前チェック

- arXivの論文ページで、現在の版の `view license` がCC BY 4.0を指すことを再確認する。
- 図のキャプションや謝辞に、写真、地図、ロゴなど第三者著作物の個別条件がないか確認する。
- 論文名、著者、原文URL、CC BY 4.0、翻訳・レイアウト変更の事実を表示する。
- PLaMoの生成物条件として `Built with PLaMo`、生成元、PLaMo Community Licenseの案内を付ける。
- 未校正の機械翻訳であり、原著が正本であることを明示する。

この一覧は法的助言ではありません。迷う素材がある場合は、その図を映さないか、著者・出版社へ確認してください。
