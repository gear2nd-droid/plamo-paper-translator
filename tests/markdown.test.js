// Run with: node --test tests/markdown.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

globalThis.katex = require(path.join(__dirname, '..', 'static', 'vendor', 'katex', 'katex.min.js'));
const {renderMarkdown, markdownToPlainText} = require(path.join(__dirname, '..', 'static', 'markdown.js'));

test('escapes raw HTML instead of rendering it', () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)> **bold**');
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(html.includes('<strong>bold</strong>'));
});

test('renders the research-note template as headings-like paragraphs and lists', () => {
  const html = renderMarkdown('研究課題\n- 長い文書の翻訳\n- \n\n## 結果\n1. BLEU が向上\n2. 速度は同等');
  assert.match(html, /<p>研究課題<\/p><ul><li>長い文書の翻訳<\/li>/);
  assert.match(html, /<h4 class="md-heading md-h2">結果<\/h4>/);
  assert.match(html, /<ol><li>BLEU が向上<\/li><li>速度は同等<\/li><\/ol>/);
});

test('keeps single line breaks inside a paragraph', () => {
  assert.equal(renderMarkdown('一行目\n二行目'), '<p>一行目<br>二行目</p>');
});

test('nests indented list items', () => {
  const html = renderMarkdown('- 手法\n  - 前処理\n  - 学習\n- 結果');
  assert.equal(html, '<ul><li>手法<ul><li>前処理</li><li>学習</li></ul></li><li>結果</li></ul>');
});

test('renders inline and display math with KaTeX', () => {
  const html = renderMarkdown('解像度は $\\sigma = 216/72$ である。\n\n$$\n\\sum_{i=1}^N |C_i| \\le 3\n$$');
  assert.match(html, /class="katex"/);
  assert.match(html, /<div class="math-display"><span class="katex-display">/);
  assert.ok(!html.includes('$'));
});

test('does not treat prices as math', () => {
  const html = renderMarkdown('費用は $5 と $10 の間');
  assert.ok(!html.includes('katex'));
  assert.ok(html.includes('$5 と $10'));
});

test('math source is never interpreted as Markdown', () => {
  const html = renderMarkdown('$a*b*c$ と *強調*');
  assert.ok(html.includes('<em>強調</em>'));
  assert.equal((html.match(/<em>/g) || []).length, 1);
});

test('invalid TeX falls back to an error rendering, not an exception', () => {
  const html = renderMarkdown('$\\frac{1}{$ と続く');
  assert.ok(typeof html === 'string');
});

test('passes references to the resolver with the embed flag', () => {
  const calls = [];
  const html = renderMarkdown('![[図2]] と [[式 4]] と [[#12]]', {
    reference: (label, embed) => { calls.push([label, embed]); return `<ref>${label}</ref>`; },
  });
  assert.deepEqual(calls, [['図2', true], ['式 4', false], ['#12', false]]);
  assert.ok(html.includes('<ref>図2</ref>'));
});

test('plain text goes through the text renderer for automatic links', () => {
  const html = renderMarkdown('図2 を参照', {text: (value) => value.replace('図2', '<a>図2</a>')});
  assert.equal(html, '<p><a>図2</a> を参照</p>');
});

test('only http links become anchors', () => {
  const html = renderMarkdown('[ok](https://arxiv.org/abs/1) [bad](javascript:alert(1))');
  assert.ok(html.includes('<a href="https://arxiv.org/abs/1"'));
  assert.ok(!html.includes('href="javascript'));
});

test('quotes and code blocks', () => {
  const html = renderMarkdown('> 引用文 [[#3]]\n\n```\n**not bold**\n```');
  assert.match(html, /^<blockquote><p>引用文 /);
  assert.ok(html.includes('<pre><code>**not bold**</code></pre>'));
});

test('plain-text preview strips Markdown syntax', () => {
  assert.equal(markdownToPlainText('## 結果\n- **重要** $x^2$ と ![[図2]]'), '結果\n重要 x^2 と 図2');
});
