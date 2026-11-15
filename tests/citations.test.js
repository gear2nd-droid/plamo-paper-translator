// Run with: node --test tests/citations.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {buildCitationIndex, findCitations} = require(path.join(__dirname, '..', 'static', 'citations.js'));

const row = (index, source, kind = 'sentence') => ({index, source, kind});

test('numbered list: single numbers, lists and ranges', () => {
  const segments = [
    row(0, 'Residual learning [1] and pooling [2, 3] help; see also [1–3].'),
    row(1, 'References', 'heading'),
    row(2, '[1] Y. Bengio. Learning long-term dependencies. 1994.', 'citation'),
    row(3, '[2] J. Long. Fully convolutional networks. In CVPR, 2015. [3] K. He. Deep residual learning. 2016.', 'citation'),
  ];
  const index = buildCitationIndex(segments);
  const found = findCitations(segments[0].source, index);
  assert.deepEqual(found.map((item) => segments[0].source.slice(item.start, item.end)), ['[1]', '[2, 3]', '[1–3]']);
  assert.equal(found[0].entries[0].index, 2);
  assert.match(found[1].entries[1].text, /^K\. He\. Deep residual learning/);
  assert.equal(found[2].entries.length, 3);
});

test('numbers that are not citations stay text', () => {
  const segments = [row(0, 'Values in [0, 1] and [256, 480].'), row(1, '1. Smith, A. A paper. 2020.', 'citation'), row(2, '2. Lee, B. Another. 2021.', 'citation'), row(3, '3. Kim, C. Third. 2022.', 'citation')];
  const index = buildCitationIndex(segments);
  assert.deepEqual(findCitations(segments[0].source, index), []);
  assert.equal(findCitations('as in [2]', index)[0].entries[0].text, 'Lee, B. Another. 2021.');
});

test('labelled list after a heading inside a sentence', () => {
  const segments = [
    row(0, 'MIL [Finn 17b] and BERT [Devlin 19, Finn 17b] are used.'),
    row(1, 'The code is public. ♢ 参考文献 ♢ [Devlin 19] Devlin, J.: BERT, NAACL (2019)'),
    row(2, '[Finn 17b] Finn, C.: One-shot imitation, CoRL (2017)'),
  ];
  const index = buildCitationIndex(segments);
  const found = findCitations(segments[0].source, index);
  assert.equal(found.length, 2);
  assert.equal(found[0].entries[0].index, 2);
  assert.deepEqual(found[1].entries.map((entry) => entry.index), [1, 2]);
});

test('author-year citations, narrative years and partial lists', () => {
  const list = [
    'Chengkai Dai, Charlie C. L. Wang, and Yong-Jin Liu. 2018. Support-Free Volume Printing. ACM Trans. Graph. 37, 4, Article 134 (July 2018), 14 pages. https://doi.org/10.1145/3197517.3201342',
    'Xiaoting Zhang, Emily Whiting, and Charlie C.L. Wang. 2015. Perceptual models of preference. ACM Transactions on Graphics 34, 6 (2015). Du Q. Huynh. 2009. Metrics for 3D rotations. Journal of Imaging 35, 2 (2009), 155–164.',
    'Tianyu Zhang, Guoxin Fang, and Charlie C. L. Wang. 2020. Reinforced FDM. ACM Trans. Graph. 39, 6 (2020).',
  ];
  const segments = [
    row(0, 'Printing [Dai et al. 2018; Unknown et al. 1999] and preference [Zhang et al. 2015]; Dai et al. [2018] also (Huynh, 2009).'),
    ...list.map((source, position) => row(position + 1, source, 'citation')),
  ];
  const index = buildCitationIndex(segments);
  const found = findCitations(segments[0].source, index);
  assert.equal(found.length, 4);
  assert.deepEqual(found[0].entries.map((entry) => entry.index), [1]);
  assert.match(found[1].entries[0].text, /^Xiaoting Zhang, .* \(2015\)\.$/);
  assert.equal(found[2].entries[0].index, 1);
  assert.match(found[3].entries[0].text, /^Du Q\. Huynh\. 2009\./);
});

test('no reference list, no links', () => {
  const index = buildCitationIndex([row(0, 'Text [1].')]);
  assert.deepEqual(findCitations('Text [1].', index), []);
});
