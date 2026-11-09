const JOB_STORAGE_KEY = 'plamo-paper-translator.job-id';
const MAX_CLIPS = 3;
const CLIP_PARTS = {
  source: {key:'E', label:'英語'},
  translation: {key:'J', label:'日本語'},
  note: {key:'M', label:'メモ'},
};
const REFERENCE_PATTERN = /\b(?:fig(?:ure)?s?\.?\s*\d+(?:\s*[-–]\s*\d+)?|eq(?:uation)?s?\.?\s*\(?\d+\)?|tables?\s+\d+)|(?:図|式|表)\s*\(?\d+\)?/gi;
const ACADEMIC_LINK_PATTERN = /https?:\/\/[^\s<>"']+|(?:doi:\s*)?10\.\d{4,9}\/[-._;()/:a-z0-9]+|arxiv:\s*\d{4}\.\d{4,5}(?:v\d+)?/gi;
const state = { documentId: null, jobId: null, poller: null, polling: false, pollFailures: 0, segments: [], segmentationMode: 'sentence', tocObserver: null, referenceTargets: new Map(), referenceHistory: [], resultsReady: false, paperTags: [], paperSummary: '', paperSourceUrl: '', paperReadingStatus: 'unread', readingIndex: 0, readingSaveTimer: null, lastSavedReadingIndex: null, paperSummaryScrollY: 0, librarySearchTimer: null, revisionSegmentIndex: null };
const noteTimers = new Map();
const $ = (id) => document.getElementById(id);

function toast(message) {
  const node = $('toast'); node.textContent = message; node.classList.add('show');
  setTimeout(() => node.classList.remove('show'), 3200);
}

function showStep(number, scrollBehavior = 'smooth') {
  document.querySelectorAll('.panel').forEach((node, i) => node.classList.toggle('active', i === number - 1));
  document.querySelectorAll('.step').forEach((node, i) => node.classList.toggle('active', i === number - 1));
  document.querySelector('.workspace').classList.toggle('reading', number === 3);
  document.body.classList.toggle('reading-mode', number === 3);
  updateReaderToolbar();
  window.scrollTo({top: document.querySelector('.workspace').offsetTop - 90, behavior: scrollBehavior});
}

async function updateEngine() {
  try {
    const response = await fetch('/api/status'); const data = await response.json();
    const badge = $('engineBadge');
    if (data.engine === 'ready') { badge.className = 'engine-badge ready'; badge.querySelector('span').textContent = data.model || 'エンジン準備完了'; }
    else { badge.className = 'engine-badge waiting'; badge.querySelector('span').textContent = data.model ? 'モデル読込中' : 'セットアップが必要'; }
  } catch (_) { $('engineBadge').className = 'engine-badge error'; }
}

async function upload(file) {
  if (!file) return;
  $('uploadState').classList.remove('hidden'); $('uploadState').innerHTML = `<div><b>${escapeHtml(file.name)}</b><p>本文を抽出しています…</p></div>`;
  $('toSettings').disabled = true;
  try {
    const response = await fetch('/api/documents', {method: 'POST', headers: {'X-Filename': encodeURIComponent(file.name), 'Content-Type': file.type || 'application/octet-stream'}, body: file});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || '読み込みに失敗しました');
    state.documentId = data.id; $('title').value = data.title;
    if (!$('sourceUrl').value && data.suggested_source_url) $('sourceUrl').value = data.suggested_source_url;
    const duplicate = Array.isArray(data.duplicates) ? data.duplicates[0] : null;
    const duplicateNotice = duplicate ? `<p class="duplicate-warning">同じPDFの翻訳がライブラリにあります。<button type="button" class="duplicate-open" data-duplicate-job="${escapeHtml(duplicate.id)}">「${escapeHtml(duplicate.title)}」を開く</button></p>` : '';
    $('uploadState').innerHTML = `<div><b>${escapeHtml(data.filename)}</b><p>本文の抽出が完了しました</p>${duplicateNotice}</div><div class="document-stats"><span>${data.pages}ページ</span><span>${data.segment_count}行</span><span>${data.characters.toLocaleString()}文字</span></div>`;
    $('toSettings').disabled = false; toast('論文を読み込みました');
  } catch (error) { $('uploadState').innerHTML = `<div><b>読み込めませんでした</b><p>${escapeHtml(error.message)}</p></div>`; toast(error.message); }
}

async function startJob() {
  if (!state.documentId) return toast('先に論文を読み込んでください');
  const payload = {document_id: state.documentId, title: $('title').value, source_url: $('sourceUrl').value, segmentation_mode: document.querySelector('input[name="segmentationMode"]:checked').value, source_lang: $('sourceLang').value, target_lang: $('targetLang').value, form: $('form').value, context: $('context').value, glossary: $('glossary').value};
  state.resultsReady = false; state.segments = []; state.paperTags = []; state.paperSummary = ''; state.paperSourceUrl = ''; state.paperReadingStatus = 'unread'; state.readingIndex = 0;
  $('step3').classList.remove('results-ready');
  showStep(3); $('progressBox').classList.remove('hidden'); $('readerLayout').classList.add('hidden'); $('results').innerHTML = '';
  try {
    const response = await fetch('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || '翻訳を開始できませんでした');
    state.jobId = data.id; localStorage.setItem(JOB_STORAGE_KEY, data.id); pollJob();
  } catch (error) { $('progressMessage').textContent = error.message; toast(error.message); }
}

function schedulePoll(delay = 1200) {
  clearTimeout(state.poller);
  state.poller = setTimeout(pollJob, delay);
}

async function pollJob() {
  if (!state.jobId || state.polling) return;
  state.polling = true;
  let terminal = false;
  try {
    const response = await fetch(`/api/jobs/${state.jobId}`); const data = await response.json();
    if (!response.ok) throw new Error(data.error || '状態を取得できません');
    state.pollFailures = 0;
    $('progressMessage').textContent = data.message; $('progressPercent').textContent = `${Math.round(data.progress || 0)}%`; $('progressBar').style.width = `${data.progress || 0}%`;
    if (['completed','completed_with_errors','failed','cancelled'].includes(data.status)) {
      terminal = true; clearTimeout(state.poller); state.poller = null;
      if (data.status.startsWith('completed')) showResults(data); else { $('resultSubtitle').textContent = data.message; toast(data.message); }
    }
  } catch (_) {
    state.pollFailures += 1;
    $('progressMessage').textContent = `接続を再試行しています…（${state.pollFailures}回目）`;
  } finally {
    state.polling = false;
    if (!terminal) schedulePoll(state.pollFailures ? Math.min(10000, 1200 * (2 ** Math.min(state.pollFailures, 3))) : 1200);
  }
}

function showResults(data) {
  $('progressBox').classList.add('hidden');
  $('readerLayout').classList.remove('hidden');
  $('readerHeader').classList.remove('hidden');
  $('step3').classList.add('results-ready');
  const mode = data.segmentation_mode || 'sentence';
  state.segmentationMode = mode;
  const loadedSegments = (data.segments || []).map((item) => ({...item, clips:itemClips(item)}));
  const referenceLayout = prepareReferenceLayout(loadedSegments);
  state.segments = referenceLayout.segments;
  state.referenceTargets = referenceLayout.targets;
  state.referenceHistory = [];
  state.paperTags = Array.isArray(data.tags) ? data.tags.filter((tag) => typeof tag === 'string') : [];
  state.paperSummary = String(data.paper_summary || '');
  state.paperSourceUrl = String(data.source_url || '');
  state.paperReadingStatus = ['unread','reading','completed','archived'].includes(data.reading_status) ? data.reading_status : 'unread';
  state.readingIndex = Math.max(0, Number(data.reading_index) || 0);
  state.lastSavedReadingIndex = state.readingIndex;
  state.resultsReady = true;
  updateReferenceBackButton();
  const resultStatus = mode === 'paragraph' ? `${data.total}段落・構造ブロックの対訳が完成しました。` : `${data.total}行の対訳が完成しました。Pは段落、Sは段落内の文番号です。`;
  $('resultSubtitle').textContent = resultStatus;
  $('topbarResultStatus').textContent = mode === 'paragraph' ? `${data.total}段落` : `${data.total}行 · P/S`;
  $('topbarResultStatus').title = resultStatus;
  updatePaperSummaryButton();
  updateReaderToolbar();
  ['Html','Md','Json'].forEach((format) => $(`export${format}`).href = `/api/jobs/${state.jobId}/export?format=${format.toLowerCase()}`);
  $('results').innerHTML = state.segments.map((item, position, list) => renderSegment(item, position, list, mode)).join('');
  document.querySelectorAll('.note-editor').forEach(resizeNote);
  renderPaperNavigation();
  renderClipShelf();
  markPaperOpened();
  refreshQualityCheck(false);
  if (state.readingIndex > 0) requestAnimationFrame(() => requestAnimationFrame(() => restoreReadingPosition(state.readingIndex)));
  toast('翻訳が完了しました');
}

function renderSegment(item, position, list, mode) {
  const kind = item.kind || 'sentence';
  const labels = {sentence:'文', heading:'見出し', equation:'数式', table:'表', figure:'図', list_item:'箇条書き', citation:'引用'};
  const translation = item.translation || `⚠ ${item.error || '未翻訳'}`;
  const imageItems = (item.assets || []).map((asset, number) => `<figure><a href="${escapeHtml(asset)}" target="_blank" rel="noreferrer"><img src="${escapeHtml(asset)}" alt="${kind === 'equation' ? '数式' : '図'} ${item.index + 1}-${number + 1}" loading="lazy"></a></figure>`).join('');
  const images = imageItems ? `<div class="figure-gallery">${imageItems}</div>` : '';
  const equationImage = kind === 'equation' && Boolean(imageItems);
  const sourceText = renderReferencedText(item.source, item.index);
  const translationText = renderReferencedText(translation, item.index);
  const sourceContent = equationImage ? `${images}<details class="equation-text"><summary>検索用テキスト</summary><p>${sourceText}</p></details>` : `${images}<p>${sourceText}</p>`;
  const revisions = Array.isArray(item.translation_revisions) ? item.translation_revisions : [];
  const originLabel = item.translation_origin === 'manual' ? '編集済み' : (item.translation_origin === 'model' ? 'モデル訳' : '');
  const translationActions = equationImage ? '' : `<div class="translation-actions"><span>${escapeHtml(originLabel)}</span><button type="button" data-translation-action="edit" data-translation-index="${item.index}">編集</button><button type="button" data-translation-action="retranslate" data-translation-index="${item.index}">再翻訳</button>${revisions.length ? `<button type="button" data-translation-action="history" data-translation-index="${item.index}">履歴 ${revisions.length}</button>` : ''}</div>`;
  const translationContent = equationImage ? `${images}<details class="equation-text"><summary>抽出テキスト</summary><p>${translationText}</p></details>` : `${translationActions}<div class="translation-body"><p>${translationText}</p></div>`;
  const hasPosition = Number(item.paragraph) > 0;
  const isParagraphStart = hasPosition && (position === 0 || item.paragraph !== list[position - 1].paragraph);
  const unitPosition = hasPosition ? (Number(item.sentence) > 0 ? `P${item.paragraph} / S${item.sentence}` : `P${item.paragraph}`) : '旧形式';
  const unitLabel = mode === 'paragraph' && kind === 'sentence' ? '段落' : (labels[kind] || kind);
  const bookmarked = Boolean(item.bookmarked);
  const clips = itemClips(item);
  const clipped = clips.length > 0;
  const clipButtons = Object.entries(CLIP_PARTS).map(([part, definition]) => {
    const active = clips.includes(part);
    return `<button type="button" class="clip-toggle clip-part-toggle" data-clip-index="${item.index}" data-clip-part="${part}" aria-pressed="${active}" aria-label="${item.index + 1}番の${definition.label}をクリップ${active ? 'から外す' : 'して固定表示'}"><b>${definition.key}</b><span>${definition.label}</span></button>`;
  }).join('');
  const headingLevel = Math.max(0, Number(item.heading_level) || (kind === 'heading' ? 1 : 0));
  return `<section id="segment-${item.index}" class="reader-row kind-${escapeHtml(kind)}${isParagraphStart ? ' paragraph-start' : ''}${bookmarked ? ' bookmarked' : ''}${clipped ? ' clipped' : ''}" data-index="${item.index}"${headingLevel ? ` data-heading-level="${headingLevel}"` : ''}>
    <div class="unit-meta"><b>${String(item.index + 1).padStart(3, '0')}</b><span>${escapeHtml(unitPosition)}</span><i>${escapeHtml(unitLabel)}</i></div>
    <article class="source-pane">${sourceContent}</article>
    <article class="translation-pane">${translationContent}</article>
    <aside class="memo-pane"><div class="annotation-actions"><button type="button" class="bookmark-toggle" data-bookmark-index="${item.index}" aria-pressed="${bookmarked}" aria-label="${item.index + 1}番をブックマーク${bookmarked ? 'から削除' : 'に追加'}"><span aria-hidden="true">${bookmarked ? '★' : '☆'}</span>${bookmarked ? '登録済み' : 'ブックマーク'}</button><span class="clip-button-group" aria-label="クリップ対象">${clipButtons}</span></div><textarea class="note-editor" rows="1" data-note-index="${item.index}" aria-label="${item.index + 1}番のメモ" placeholder="この箇所へのメモ…">${escapeHtml(item.note || '')}</textarea><small class="note-status">${item.note ? '保存済み' : '未入力'}</small></aside>
  </section>`;
}

function navigationLabel(item, limit = 68) {
  const compact = String(item.source || '').replace(/\s+/g, ' ').trim();
  return compact.length > limit ? `${compact.slice(0, limit - 1)}…` : compact;
}

function referenceKey(type, number) {
  return `${type}:${Number(number)}`;
}

function segmentReferenceDeclarations(item) {
  const declarations = new Map();
  const add = (type, number) => {
    if (number) declarations.set(referenceKey(type, number), {type, number:String(Number(number))});
  };
  const labelEquation = String(item.label || '').match(/^equation:(\d+)$/i);
  if (labelEquation) add('equation', labelEquation[1]);
  [item.source, item.translation].forEach((value) => {
    const text = String(value || '').trim();
    const captionSuffix = hasVisualAsset(item) ? '(?:[.．:：]|\\s|$)' : '(?:[.．:：]|$)';
    const figure = text.match(new RegExp(`^(?:figure|fig\\.?)\\s*(\\d+)\\s*${captionSuffix}`, 'i'))
      || text.match(new RegExp(`^図\\s*(\\d+)\\s*${captionSuffix}`));
    const table = text.match(new RegExp(`^table\\s*(\\d+)\\s*${captionSuffix}`, 'i'))
      || text.match(new RegExp(`^表\\s*(\\d+)\\s*${captionSuffix}`));
    if (figure) add('figure', figure[1]);
    if (table) add('table', table[1]);
  });
  if ((item.kind || '') === 'equation') {
    const trailingNumber = String(item.source || '').match(/\(\s*(\d+)\s*\)\s*$/);
    if (trailingNumber) add('equation', trailingNumber[1]);
  }
  return [...declarations.values()];
}

function hasVisualAsset(item) {
  return Array.isArray(item.assets) && item.assets.length > 0;
}

function associatedVisualCandidate(segments, declarationPosition, identity) {
  if (!['figure', 'equation'].includes(identity.type)) return null;
  const declaration = segments[declarationPosition];
  const candidates = [];
  segments.forEach((item, position) => {
    if (!hasVisualAsset(item) || position === declarationPosition) return;
    if (Number(item.page) !== Number(declaration.page)) return;
    const distance = Math.abs(position - declarationPosition);
    if (distance > 6) return;
    const conflictingDeclaration = segmentReferenceDeclarations(item)
      .some((entry) => entry.type === identity.type && entry.number !== identity.number);
    if (conflictingDeclaration) return;
    const sameParagraph = Number(declaration.paragraph) > 0
      && Number(item.paragraph) === Number(declaration.paragraph);
    const afterDeclaration = position > declarationPosition;
    const kindMatches = (identity.type === 'figure' && (item.kind || '') === 'figure')
      || (identity.type === 'equation' && (item.kind || '') === 'equation');
    const score = (sameParagraph ? 900 : 600)
      + (afterDeclaration ? 30 : 0)
      + (kindMatches ? 20 : 0)
      - distance;
    candidates.push({item, position, score});
  });
  candidates.sort((left, right) => right.score - left.score || left.position - right.position);
  return candidates[0] || null;
}

function buildReferenceCatalog(segments) {
  const catalog = new Map();
  const register = (identity, item, position, score) => {
    const key = referenceKey(identity.type, identity.number);
    const existing = catalog.get(key);
    if (!existing || score > existing.score || (score === existing.score && position < existing.position)) {
      catalog.set(key, {...identity, item, position, score});
    }
  };
  segments.forEach((item, position) => {
    segmentReferenceDeclarations(item).forEach((identity) => {
      if (hasVisualAsset(item)) {
        register(identity, item, position, 1200);
        return;
      }
      const associated = associatedVisualCandidate(segments, position, identity);
      if (associated) register(identity, associated.item, associated.position, associated.score);
      register(identity, item, position, 100);
    });
  });
  return catalog;
}

function findFirstReferenceAnchors(segments, targets) {
  const anchors = new Map();
  segments.forEach((item, position) => {
    [item.source, item.translation].forEach((value) => {
      for (const match of String(value || '').matchAll(REFERENCE_PATTERN)) {
        const identity = referenceIdentity(match[0]);
        if (!identity) continue;
        const key = referenceKey(identity.type, identity.number);
        const target = targets.get(key);
        if (!Number.isInteger(target) || target === Number(item.index) || anchors.has(key)) continue;
        anchors.set(key, {item, position});
      }
    });
  });
  return anchors;
}

function prepareReferenceLayout(segments) {
  const catalog = buildReferenceCatalog(segments);
  const targets = new Map([...catalog].map(([key, entry]) => [key, Number(entry.item.index)]));
  const anchors = findFirstReferenceAnchors(segments, targets);
  const plans = [...catalog.entries()].flatMap(([key, entry]) => {
    const anchor = anchors.get(key);
    const movable = ['figure', 'equation'].includes(entry.type) && hasVisualAsset(entry.item);
    return movable && anchor && anchor.position < entry.position
      ? [{anchor, target:entry.item, targetPosition:entry.position}]
      : [];
  }).sort((left, right) => left.anchor.position - right.anchor.position || left.targetPosition - right.targetPosition);

  const arranged = [...segments];
  const anchorTails = new Map();
  plans.forEach(({anchor, target}) => {
    const anchorKey = Number(anchor.item.index);
    const insertionAnchor = anchorTails.get(anchorKey) || anchor.item;
    let targetPosition = arranged.indexOf(target);
    let anchorPosition = arranged.indexOf(insertionAnchor);
    if (targetPosition < 0 || anchorPosition < 0) return;
    if (targetPosition === anchorPosition + 1) {
      anchorTails.set(anchorKey, target);
      return;
    }
    arranged.splice(targetPosition, 1);
    anchorPosition = arranged.indexOf(insertionAnchor);
    arranged.splice(anchorPosition + 1, 0, target);
    target.reference_relocated = true;
    anchorTails.set(anchorKey, target);
  });
  return {segments: arranged, targets};
}

function referenceIdentity(text) {
  const number = String(text).match(/\d+/)?.[0];
  if (!number) return null;
  if (/^(?:fig|figure|図)/i.test(text)) return {type:'figure', number};
  if (/^(?:eq|equation|式)/i.test(text)) return {type:'equation', number};
  if (/^(?:table|表)/i.test(text)) return {type:'table', number};
  return null;
}

function renderReferencedText(value, currentIndex) {
  const text = String(value ?? '');
  let rendered = '';
  let lastIndex = 0;
  const matches = [
    ...[...text.matchAll(REFERENCE_PATTERN)].map((match) => ({match, kind:'reference'})),
    ...[...text.matchAll(ACADEMIC_LINK_PATTERN)].map((match) => ({match, kind:'academic'})),
  ].sort((left, right) => left.match.index - right.match.index || (left.kind === 'academic' ? -1 : 1));
  for (const entry of matches) {
    const match = entry.match;
    if (match.index < lastIndex) continue;
    rendered += escapeHtml(text.slice(lastIndex, match.index));
    if (entry.kind === 'academic') {
      const visible = match[0].replace(/[.,;]+$/, '');
      const doi = visible.match(/10\.\d{4,9}\/[-._;()/:a-z0-9]+/i)?.[0];
      const arxiv = visible.match(/\d{4}\.\d{4,5}(?:v\d+)?/i)?.[0];
      const href = /^https?:\/\//i.test(visible) ? visible : (doi ? `https://doi.org/${doi}` : `https://arxiv.org/abs/${arxiv}`);
      rendered += `<a class="academic-link" href="${escapeHtml(href)}" target="_blank" rel="noreferrer" title="原文情報を開く">${escapeHtml(visible)}</a>${escapeHtml(match[0].slice(visible.length))}`;
    } else {
      const identity = referenceIdentity(match[0]);
      const target = identity ? state.referenceTargets.get(referenceKey(identity.type, identity.number)) : undefined;
      if (Number.isInteger(target) && target !== Number(currentIndex)) {
        rendered += `<button type="button" class="reference-link" data-reference-index="${target}" title="参照先へ移動">${escapeHtml(match[0])}</button>`;
      } else {
        rendered += escapeHtml(match[0]);
      }
    }
    lastIndex = match.index + match[0].length;
  }
  return rendered + escapeHtml(text.slice(lastIndex));
}

function renderPaperNavigation() {
  const headings = state.segments.filter((item) => Number(item.heading_level) > 0 || (item.kind || '') === 'heading');
  $('tableOfContents').innerHTML = headings.length
    ? headings.map((item) => {
        const level = Math.min(6, Math.max(1, Number(item.heading_level) || 1));
        return `<button type="button" class="toc-link level-${level}" data-nav-index="${item.index}"><span>${escapeHtml(navigationLabel(item))}</span><small>p.${Number(item.page) || 1}</small></button>`;
      }).join('')
    : '<p class="navigation-empty">見出しを検出できませんでした。</p>';
  const visuals = state.segments.filter((item) => ['figure','equation','table'].includes(item.kind || '') || (Array.isArray(item.assets) && item.assets.length));
  const visualLabels = {figure:'図', equation:'数式', table:'表'};
  $('visualList').innerHTML = visuals.length
    ? visuals.map((item) => `<button type="button" class="visual-link" data-nav-index="${item.index}"><b>${escapeHtml(visualLabels[item.kind] || '図')}</b><span>${escapeHtml(navigationLabel(item, 42) || `${Number(item.page) || 1}ページの画像`)}</span><small>${String(Number(item.index) + 1).padStart(3, '0')}</small></button>`).join('')
    : '<p class="navigation-empty">図表・数式はありません。</p>';
  renderBookmarkList();
  observeTableOfContents(headings);
}

function renderBookmarkList() {
  const bookmarked = state.segments.filter((item) => Boolean(item.bookmarked));
  $('bookmarkList').innerHTML = bookmarked.length
    ? bookmarked.map((item) => `<button type="button" class="bookmark-link" data-nav-index="${item.index}"><span aria-hidden="true">★</span><b>${escapeHtml(navigationLabel(item, 54))}</b><small>${String(item.index + 1).padStart(3, '0')}</small></button>`).join('')
    : '<p class="navigation-empty">右側の☆から追加できます。</p>';
}

function itemClips(item) {
  const clips = Array.isArray(item?.clips) ? item.clips.filter((part) => Boolean(CLIP_PARTS[part])) : [];
  if (!clips.length && item?.clipped) return ['source'];
  return [...new Set(clips)];
}

function totalClipCount() {
  return state.segments.reduce((count, item) => count + itemClips(item).length, 0);
}

function renderClipShelf() {
  const clipped = state.segments.flatMap((item) => itemClips(item).map((part) => ({item, part})));
  $('clipShelf').classList.toggle('hidden', clipped.length === 0);
  $('clipCount').textContent = `${clipped.length} / ${MAX_CLIPS}`;
  $('clipShelfItems').innerHTML = clipped.map(({item, part}) => renderClipCard(item, part)).join('');
}

function renderClipCard(item, part) {
  const kind = item.kind || 'sentence';
  const labels = {sentence:'文', heading:'見出し', equation:'数式', table:'表', figure:'図', list_item:'箇条書き', citation:'引用'};
  const definition = CLIP_PARTS[part];
  const translation = item.translation || `⚠ ${item.error || '未翻訳'}`;
  const text = part === 'source' ? item.source : (part === 'translation' ? translation : (item.note || '（メモは未入力です）'));
  const renderedText = part === 'note' ? escapeHtml(text) : renderReferencedText(text, item.index);
  const assets = part === 'note' ? '' : (item.assets || []).map((asset, number) => `<figure><a href="${escapeHtml(asset)}" target="_blank" rel="noreferrer"><img src="${escapeHtml(asset)}" alt="${kind === 'equation' ? '数式' : '図'} ${item.index + 1}-${number + 1}"></a></figure>`).join('');
  return `<article class="clip-card clip-${part} kind-${escapeHtml(kind)}" data-clip-index="${item.index}" data-clip-part="${part}">
    <header><button type="button" class="clip-jump" data-nav-index="${item.index}" title="元の位置へ移動">${String(item.index + 1).padStart(3, '0')}</button><span><b>${definition.key}</b> ${definition.label} · ${escapeHtml(labels[kind] || kind)}</span><button type="button" class="clip-toggle clip-remove" data-clip-index="${item.index}" data-clip-part="${part}" aria-label="${item.index + 1}番の${definition.label}をクリップから外す">×</button></header>
    ${assets ? `<div class="clip-assets">${assets}</div>` : ''}
    <div class="clip-card-body"><p>${renderedText}</p></div>
  </article>`;
}

function observeTableOfContents(headings) {
  if (state.tocObserver) state.tocObserver.disconnect();
  if (!headings.length || !('IntersectionObserver' in window)) return;
  state.tocObserver = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
    if (!visible.length) return;
    const index = visible[0].target.dataset.index;
    document.querySelectorAll('.toc-link').forEach((link) => link.classList.toggle('active', link.dataset.navIndex === index));
  }, {rootMargin: '-120px 0px -68% 0px', threshold: 0});
  headings.forEach((item) => {
    const row = $(`segment-${item.index}`);
    if (row) state.tocObserver.observe(row);
  });
}

function jumpToSegment(index) {
  const row = $(`segment-${index}`);
  if (!row) return;
  if (row.classList.contains('filtered')) {
    $('readerSearch').value = '';
    document.querySelectorAll('.reader-row').forEach((item) => item.classList.remove('filtered'));
  }
  const stickyHeight = $('readerStickyStack')?.offsetHeight || 0;
  const top = row.getBoundingClientRect().top + window.scrollY - stickyHeight - 86;
  window.scrollTo({top: Math.max(0, top), behavior: 'smooth'});
  row.classList.add('jump-highlight');
  setTimeout(() => row.classList.remove('jump-highlight'), 1300);
}

function updateReferenceBackButton() {
  $('referenceBack').classList.toggle('hidden', state.referenceHistory.length === 0);
}

function jumpToReference(index) {
  state.referenceHistory.push(window.scrollY);
  if (state.referenceHistory.length > 20) state.referenceHistory.shift();
  updateReferenceBackButton();
  jumpToSegment(index);
}

function returnFromReference() {
  const top = state.referenceHistory.pop();
  updateReferenceBackButton();
  if (Number.isFinite(top)) window.scrollTo({top, behavior:'smooth'});
}

function restoreReadingPosition(index) {
  const row = $(`segment-${index}`);
  if (!row) return;
  const stickyHeight = $('readerStickyStack')?.offsetHeight || 0;
  const top = row.getBoundingClientRect().top + window.scrollY - stickyHeight - 86;
  window.scrollTo({top: Math.max(0, top), behavior: 'auto'});
}

function currentReadingIndex() {
  const rows = [...document.querySelectorAll('.reader-row:not(.filtered)')];
  if (!rows.length) return 0;
  const threshold = 130 + ($('readerStickyStack')?.offsetHeight || 0);
  let current = rows[0];
  for (const row of rows) {
    if (row.getBoundingClientRect().top <= threshold) current = row;
    else break;
  }
  return Math.max(0, Number(current.dataset.index) || 0);
}

async function saveReadingPosition(index, markOpened = false) {
  if (!state.jobId || !state.resultsReady) return;
  if (!markOpened && index === state.lastSavedReadingIndex) return;
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/metadata`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reading_index:index, mark_opened:markOpened})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '読書位置を保存できません');
    state.readingIndex = Number(data.reading_index) || 0;
    state.lastSavedReadingIndex = state.readingIndex;
    state.paperReadingStatus = data.reading_status || state.paperReadingStatus;
  } catch (_) {
    // Reading progress is best-effort; annotations and translations remain unaffected.
  }
}

function markPaperOpened() {
  saveReadingPosition(state.readingIndex, true);
}

function scheduleReadingPositionSave() {
  if (!state.resultsReady) return;
  clearTimeout(state.readingSaveTimer);
  state.readingSaveTimer = setTimeout(() => saveReadingPosition(currentReadingIndex()), 1200);
}

const READING_STATUS_LABELS = {unread:'未読', reading:'読書中', completed:'読了', archived:'保留'};

function formatLibraryDate(value) {
  if (!value) return '日時不明';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '日時不明' : new Intl.DateTimeFormat('ja-JP', {year:'numeric', month:'short', day:'numeric'}).format(date);
}

function renderLibraryItem(item) {
  const tags = Array.isArray(item.tags) ? item.tags.slice(0, 8).map((tag) => `<span>${escapeHtml(tag)}</span>`).join('') : '';
  const summary = String(item.summary || '').trim();
  const progress = Math.max(0, Math.min(100, Number(item.reading_progress) || 0));
  const source = item.source_url ? `<a class="library-source" href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">原文を開く ↗</a>` : '';
  return `<article class="library-card">
    <div class="library-card-main"><h3>${escapeHtml(item.title || 'Untitled')}</h3>
      <div class="library-card-meta"><span class="library-state">${escapeHtml(READING_STATUS_LABELS[item.reading_status] || item.reading_status)}</span><span>${Number(item.total) || 0}セグメント</span><span>更新 ${escapeHtml(formatLibraryDate(item.updated_at || item.created_at))}</span></div>
      ${tags ? `<div class="library-tags">${tags}</div>` : ''}
      ${summary ? `<p class="library-summary">${escapeHtml(summary)}</p>` : ''}
      ${item.match ? `<p class="library-match">${escapeHtml(item.match)}</p>` : ''}
      <div class="library-progress" title="読書位置 ${progress}%"><i style="width:${progress}%"></i></div>
    </div>
    <div class="library-card-actions"><button type="button" class="library-open" data-library-job="${escapeHtml(item.id)}">続きから読む</button>${source}</div>
  </article>`;
}

async function loadLibrary() {
  const query = $('librarySearch').value.trim();
  const status = $('libraryStatus').value;
  $('libraryItems').innerHTML = '<div class="library-empty">ライブラリを検索しています…</div>';
  try {
    const response = await fetch(`/api/library?q=${encodeURIComponent(query)}&status=${encodeURIComponent(status)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'ライブラリを読み込めません');
    $('libraryCount').textContent = query ? `${data.count}件が一致しました` : `${data.count}件の論文`;
    $('libraryDataDir').textContent = `保存先: ${data.data_dir || 'data/'}`;
    $('libraryItems').innerHTML = data.items?.length ? data.items.map(renderLibraryItem).join('') : `<div class="library-empty">${query ? '一致する論文がありません。' : '翻訳した論文がここに並びます。'}</div>`;
  } catch (error) {
    $('libraryItems').innerHTML = `<div class="library-empty">${escapeHtml(error.message || 'ライブラリを読み込めません')}</div>`;
  }
}

async function restoreLibrary(file) {
  if (!file) return;
  if (!file.name.toLocaleLowerCase().endsWith('.zip')) {
    toast('PLaMo Paper TranslatorのバックアップZIPを選んでください');
    return;
  }
  const input = $('libraryRestoreFile');
  input.disabled = true;
  $('libraryCount').textContent = 'バックアップを検査・復元しています…';
  try {
    const response = await fetch('/api/library/restore', {method:'POST', headers:{'Content-Type':'application/zip'}, body:file});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'バックアップを復元できません');
    toast(`${data.restored_jobs}件の論文を復元しました（既存${data.skipped_files}ファイルは保持）`);
    await loadLibrary();
  } catch (error) {
    $('libraryCount').textContent = '復元できませんでした';
    toast(error.message || 'バックアップを復元できません');
  } finally {
    input.disabled = false;
    input.value = '';
  }
}

function openLibraryDialog() {
  $('libraryDialog').showModal();
  loadLibrary();
  requestAnimationFrame(() => $('librarySearch').focus({preventScroll:true}));
}

function closeLibraryDialog() {
  if ($('libraryDialog').open) $('libraryDialog').close();
}

function openLibraryJob(jobId) {
  if (!/^[a-f0-9]{32}$/.test(jobId || '')) return;
  localStorage.setItem(JOB_STORAGE_KEY, jobId);
  location.href = `/?job=${encodeURIComponent(jobId)}`;
}

function applyBookmark(index, bookmarked) {
  const item = state.segments.find((segment) => Number(segment.index) === index);
  if (item) item.bookmarked = bookmarked;
  const row = $(`segment-${index}`);
  if (row) row.classList.toggle('bookmarked', bookmarked);
  const button = document.querySelector(`.bookmark-toggle[data-bookmark-index="${index}"]`);
  if (button) {
    button.setAttribute('aria-pressed', String(bookmarked));
    button.setAttribute('aria-label', `${index + 1}番をブックマーク${bookmarked ? 'から削除' : 'に追加'}`);
    button.innerHTML = `<span aria-hidden="true">${bookmarked ? '★' : '☆'}</span>${bookmarked ? '登録済み' : 'ブックマーク'}`;
  }
  renderBookmarkList();
}

async function toggleBookmark(button) {
  const index = Number(button.dataset.bookmarkIndex);
  const item = state.segments.find((segment) => Number(segment.index) === index);
  if (!item || button.disabled) return;
  const previous = Boolean(item.bookmarked);
  const next = !previous;
  button.disabled = true;
  applyBookmark(index, next);
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/bookmarks`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index, bookmarked:next})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'ブックマークを保存できません');
    applyBookmark(index, Boolean(data.bookmarked));
  } catch (error) {
    applyBookmark(index, previous);
    toast(error.message || 'ブックマークを保存できません');
  } finally {
    button.disabled = false;
  }
}

function applyClip(index, part, clipped) {
  const item = state.segments.find((segment) => Number(segment.index) === index);
  if (!item || !CLIP_PARTS[part]) return;
  const clips = new Set(itemClips(item));
  if (clipped) clips.add(part); else clips.delete(part);
  item.clips = [...clips];
  item.clipped = item.clips.length > 0;
  const row = $(`segment-${index}`);
  if (row) row.classList.toggle('clipped', item.clipped);
  const button = document.querySelector(`.memo-pane .clip-toggle[data-clip-index="${index}"][data-clip-part="${part}"]`);
  if (button) {
    button.setAttribute('aria-pressed', String(clipped));
    button.setAttribute('aria-label', `${index + 1}番の${CLIP_PARTS[part].label}をクリップ${clipped ? 'から外す' : 'して固定表示'}`);
  }
  renderClipShelf();
}

async function toggleClip(button) {
  const index = Number(button.dataset.clipIndex);
  const part = button.dataset.clipPart;
  const item = state.segments.find((segment) => Number(segment.index) === index);
  if (!item || !CLIP_PARTS[part] || button.disabled) return;
  const previous = itemClips(item).includes(part);
  const next = !previous;
  if (next && totalClipCount() >= MAX_CLIPS) {
    toast(`クリップは${MAX_CLIPS}件までです。先に1件外してください`);
    return;
  }
  button.disabled = true;
  applyClip(index, part, next);
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/clips`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index, part, clipped:next})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'クリップを保存できません');
    applyClip(index, part, Boolean(data.clipped));
  } catch (error) {
    applyClip(index, part, previous);
    toast(error.message || 'クリップを保存できません');
  } finally {
    const currentButton = document.querySelector(`.memo-pane .clip-toggle[data-clip-index="${index}"][data-clip-part="${part}"]`);
    if (currentButton) currentButton.disabled = false;
  }
}

function resizeNote(editor) {
  editor.style.height = 'auto';
  editor.style.height = `${editor.scrollHeight}px`;
}

async function saveNote(index, note, status) {
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/notes`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index, note})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'メモを保存できません');
    status.textContent = '保存済み'; status.className = 'note-status saved';
  } catch (_) {
    status.textContent = '保存を再試行します'; status.className = 'note-status error';
    noteTimers.set(index, setTimeout(() => saveNote(index, note, status), 3000));
  }
}

function rerenderSegment(index) {
  const item = state.segments.find((segment) => Number(segment.index) === Number(index));
  const position = state.segments.indexOf(item);
  const row = $(`segment-${index}`);
  if (!item || position < 0 || !row) return;
  row.outerHTML = renderSegment(item, position, state.segments, state.segmentationMode);
  const editor = document.querySelector(`.note-editor[data-note-index="${index}"]`);
  if (editor) resizeNote(editor);
  renderClipShelf();
}

function beginTranslationEdit(index) {
  const item = state.segments.find((segment) => Number(segment.index) === Number(index));
  const pane = $(`segment-${index}`)?.querySelector('.translation-pane');
  const body = pane?.querySelector('.translation-body');
  if (!item || !body) return;
  pane.classList.add('editing');
  body.innerHTML = `<textarea class="translation-editor" maxlength="50000" aria-label="${index + 1}番の訳文を編集">${escapeHtml(item.translation || '')}</textarea><div class="translation-edit-actions"><button type="button" class="secondary small" data-translation-action="cancel" data-translation-index="${index}">キャンセル</button><button type="button" class="primary small" data-translation-action="save" data-translation-index="${index}">保存</button></div>`;
  const editor = body.querySelector('.translation-editor');
  editor.style.height = `${Math.max(90, editor.scrollHeight)}px`;
  editor.focus({preventScroll:true});
}

async function saveSegmentTranslation(index, translation) {
  const response = await fetch(`/api/jobs/${state.jobId}/translations`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index, translation})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '訳文を保存できません');
  const item = state.segments.find((segment) => Number(segment.index) === Number(index));
  if (item) Object.assign(item, {
    translation:data.translation,
    translation_origin:data.translation_origin,
    translation_edited_at:data.translation_edited_at,
    translation_revisions:data.translation_revisions || [],
    error:'',
  });
  rerenderSegment(index);
  refreshQualityCheck(false);
  return data;
}

async function submitTranslationEdit(index, button) {
  const editor = $(`segment-${index}`)?.querySelector('.translation-editor');
  if (!editor) return;
  button.disabled = true;
  try {
    await saveSegmentTranslation(index, editor.value);
    toast('訳文を保存しました');
  } catch (error) {
    button.disabled = false;
    toast(error.message || '訳文を保存できません');
  }
}

async function retranslateSegment(index, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '再翻訳中…';
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/retranslate`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '再翻訳できません');
    const item = state.segments.find((segment) => Number(segment.index) === Number(index));
    if (item) Object.assign(item, {
      translation:data.translation,
      translation_origin:data.translation_origin,
      translation_edited_at:data.translation_edited_at,
      translation_revisions:data.translation_revisions || [],
      error:'',
    });
    rerenderSegment(index);
    refreshQualityCheck(false);
    toast('このセグメントを再翻訳しました');
  } catch (error) {
    button.disabled = false;
    button.textContent = original;
    toast(error.message || '再翻訳できません');
  }
}

function openTranslationHistory(index) {
  const item = state.segments.find((segment) => Number(segment.index) === Number(index));
  if (!item) return;
  state.revisionSegmentIndex = Number(index);
  const revisions = Array.isArray(item.translation_revisions) ? item.translation_revisions : [];
  const cards = [...revisions].map((revision, position) => ({...revision, position})).reverse().map((revision) => `<article class="revision-card"><header><b>${revision.origin === 'manual' ? '手動編集' : 'モデル訳'}</b><time>${escapeHtml(formatLibraryDate(revision.changed_at))}</time><button type="button" data-revision-restore="${revision.position}">この版に戻す</button></header><p>${escapeHtml(revision.translation || '')}</p></article>`).join('');
  $('revisionCurrent').textContent = item.translation || '';
  $('revisionItems').innerHTML = cards || '<p class="navigation-empty">以前の訳文はありません。</p>';
  $('revisionDialog').showModal();
}

function closeTranslationHistory() {
  if ($('revisionDialog').open) $('revisionDialog').close();
  state.revisionSegmentIndex = null;
}

async function restoreTranslationRevision(position, button) {
  const item = state.segments.find((segment) => Number(segment.index) === state.revisionSegmentIndex);
  const revision = item?.translation_revisions?.[position];
  if (!item || !revision) return;
  button.disabled = true;
  try {
    await saveSegmentTranslation(item.index, revision.translation);
    closeTranslationHistory();
    toast('以前の訳文を復元しました');
  } catch (error) {
    button.disabled = false;
    toast(error.message || '訳文を復元できません');
  }
}

function updateReaderToolbar() {
  const stepThreeActive = $('step3').classList.contains('active');
  const visible = state.resultsReady && stepThreeActive;
  $('resultActions').classList.toggle('hidden', !visible);
  $('paperSummaryButton').classList.toggle('hidden', !visible);
  $('qualityButton').classList.toggle('hidden', !visible);
}

function renderQualityIssues(data) {
  $('qualityCount').textContent = data.count ? String(data.count) : '';
  $('qualityButton').dataset.hasIssues = String(Boolean(data.count));
  $('qualitySummary').textContent = data.count ? `${data.checked}セグメントを検査し、${data.count}件の確認候補が見つかりました。` : `${data.checked}セグメントを検査し、機械的な問題候補は見つかりませんでした。`;
  $('qualityItems').innerHTML = data.issues?.length ? data.issues.map((issue) => `<article class="quality-item" data-severity="${escapeHtml(issue.severity)}"><i class="quality-severity" aria-hidden="true"></i><div><b>${escapeHtml(issue.message)}</b><small>${escapeHtml(issue.detail || '')}</small></div><button type="button" class="quality-jump" data-quality-index="${Number(issue.index)}">${String(Number(issue.index) + 1).padStart(3, '0')}へ</button></article>`).join('') : '<div class="quality-ok">機械チェック上の問題候補はありません。<br>公開・投稿前には人による校正を行ってください。</div>';
}

async function refreshQualityCheck(renderDialog = true) {
  if (!state.jobId || !state.resultsReady) return;
  if (renderDialog) {
    $('qualitySummary').textContent = '検査しています…';
    $('qualityItems').innerHTML = '<div class="library-empty">原文と訳文を照合しています…</div>';
  }
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/quality`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '翻訳を検査できません');
    renderQualityIssues(data);
  } catch (error) {
    if (renderDialog) $('qualityItems').innerHTML = `<div class="library-empty">${escapeHtml(error.message || '翻訳を検査できません')}</div>`;
  }
}

function openQualityDialog() {
  if (!state.resultsReady) return;
  $('qualityDialog').showModal();
  refreshQualityCheck(true);
}

function closeQualityDialog() {
  if ($('qualityDialog').open) $('qualityDialog').close();
}

function parsePaperTags(value) {
  return [...new Set(String(value || '').split(/[,、\n]+/).map((tag) => tag.replace(/\s+/g, ' ').trim()).filter(Boolean))];
}

function renderPaperTagPreview() {
  const tags = parsePaperTags($('paperTags').value);
  $('paperTagPreview').innerHTML = tags.length
    ? tags.slice(0, 20).map((tag) => `<span>${escapeHtml(tag)}</span>`).join('') + (tags.length > 20 ? `<small>ほか${tags.length - 20}件</small>` : '')
    : '<small>タグはまだありません。</small>';
}

function updatePaperSummaryLength() {
  $('paperSummaryLength').textContent = `${$('paperSummaryText').value.length.toLocaleString()} / 20,000`;
}

function insertPaperSummaryTemplate() {
  const template = `研究課題\n- \n\n提案・手法\n- \n\nデータ／実験条件\n- \n\n主要な結果\n- \n\n新規性・貢献\n- \n\n限界・注意点\n- \n\n次に確認すること\n- `;
  const editor = $('paperSummaryText');
  editor.value = editor.value.trim() ? `${editor.value.trimEnd()}\n\n---\n\n${template}` : template;
  updatePaperSummaryLength();
  editor.focus({preventScroll:true});
}

function updatePaperSummaryButton() {
  const hasContent = state.paperTags.length > 0 || Boolean(state.paperSummary.trim());
  $('paperSummaryButton').dataset.hasContent = String(hasContent);
  $('paperSummaryCount').textContent = state.paperTags.length ? String(state.paperTags.length) : (hasContent ? '●' : '');
  $('paperSummaryButton').setAttribute('aria-label', hasContent ? `論文のまとめを編集（タグ${state.paperTags.length}件）` : '論文のまとめを作成');
}

function openPaperSummaryDialog() {
  if (!state.jobId || !state.resultsReady) return;
  state.paperSummaryScrollY = window.scrollY;
  document.body.style.top = `-${state.paperSummaryScrollY}px`;
  document.body.classList.add('paper-summary-open');
  $('paperTags').value = state.paperTags.join(', ');
  $('paperSourceUrl').value = state.paperSourceUrl;
  $('paperReadingStatus').value = state.paperReadingStatus;
  $('paperSummaryText').value = state.paperSummary;
  $('paperSummaryStatus').textContent = 'このPC内の翻訳ジョブに保存されます。';
  $('paperSummaryStatus').className = 'dialog-status';
  renderPaperTagPreview();
  updatePaperSummaryLength();
  $('paperSummaryDialog').showModal();
  requestAnimationFrame(() => {
    $('paperTags').focus({preventScroll: true});
  });
}

function closePaperSummaryDialog() {
  if ($('paperSummaryDialog').open) $('paperSummaryDialog').close();
  document.body.classList.remove('paper-summary-open');
  document.body.style.removeProperty('top');
  window.scrollTo({top: state.paperSummaryScrollY, behavior: 'auto'});
  requestAnimationFrame(() => window.scrollTo({top: state.paperSummaryScrollY, behavior: 'auto'}));
}

async function savePaperSummary(event) {
  event.preventDefault();
  const tags = parsePaperTags($('paperTags').value);
  const summary = $('paperSummaryText').value;
  if (tags.length > 20) {
    $('paperSummaryStatus').textContent = 'タグは20件までです。';
    $('paperSummaryStatus').className = 'dialog-status error';
    return;
  }
  if (tags.some((tag) => tag.length > 40)) {
    $('paperSummaryStatus').textContent = 'タグは1件40文字以内にしてください。';
    $('paperSummaryStatus').className = 'dialog-status error';
    return;
  }
  const button = $('paperSummarySave');
  button.disabled = true;
  $('paperSummaryStatus').textContent = '保存しています…';
  $('paperSummaryStatus').className = 'dialog-status saving';
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/summary`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tags, summary, source_url:$('paperSourceUrl').value, reading_status:$('paperReadingStatus').value})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || '論文のまとめを保存できません');
    state.paperTags = data.tags || [];
    state.paperSummary = data.summary || '';
    state.paperSourceUrl = data.source_url || '';
    state.paperReadingStatus = data.reading_status || 'unread';
    updatePaperSummaryButton();
    closePaperSummaryDialog();
    toast('論文のまとめを保存しました');
  } catch (error) {
    $('paperSummaryStatus').textContent = error.message || '論文のまとめを保存できません';
    $('paperSummaryStatus').className = 'dialog-status error';
  } finally {
    button.disabled = false;
  }
}

function escapeHtml(value) { const node = document.createElement('div'); node.textContent = String(value ?? ''); return node.innerHTML; }

const dropzone = $('dropzone');
$('fileInput').addEventListener('change', (event) => upload(event.target.files[0]));
['dragenter','dragover'].forEach((name) => dropzone.addEventListener(name, (event) => {event.preventDefault(); dropzone.classList.add('drag');}));
['dragleave','drop'].forEach((name) => dropzone.addEventListener(name, (event) => {event.preventDefault(); dropzone.classList.remove('drag');}));
dropzone.addEventListener('drop', (event) => upload(event.dataTransfer.files[0]));
$('uploadState').addEventListener('click', (event) => {
  const button = event.target.closest('[data-duplicate-job]');
  if (button) openLibraryJob(button.dataset.duplicateJob);
});
$('toSettings').addEventListener('click', () => showStep(2));
document.querySelectorAll('.back').forEach((node) => node.addEventListener('click', () => showStep(Number(node.dataset.target))));
document.querySelectorAll('.step').forEach((node) => node.addEventListener('click', () => { const n=Number(node.dataset.step); if(n===1 || (n===2 && state.documentId) || (n===3 && state.jobId)) showStep(n); }));
$('targetLang').addEventListener('change', () => $('form').closest('label').style.display = $('targetLang').value === 'ja' ? '' : 'none');
$('startTranslation').addEventListener('click', startJob);
$('cancelJob').addEventListener('click', async () => { if(state.jobId) await fetch(`/api/jobs/${state.jobId}/cancel`, {method:'POST'}); });
$('newPaper').addEventListener('click', () => { localStorage.removeItem(JOB_STORAGE_KEY); location.href = '/'; });
$('referenceBack').addEventListener('click', returnFromReference);
$('libraryButton').addEventListener('click', openLibraryDialog);
$('libraryClose').addEventListener('click', closeLibraryDialog);
$('libraryDialog').addEventListener('click', (event) => { if (event.target === $('libraryDialog')) closeLibraryDialog(); });
$('libraryItems').addEventListener('click', (event) => {
  const button = event.target.closest('[data-library-job]');
  if (button) openLibraryJob(button.dataset.libraryJob);
});
$('librarySearch').addEventListener('input', () => {
  clearTimeout(state.librarySearchTimer);
  state.librarySearchTimer = setTimeout(loadLibrary, 260);
});
$('libraryStatus').addEventListener('change', loadLibrary);
$('libraryRestoreFile').addEventListener('change', (event) => restoreLibrary(event.target.files[0]));
$('qualityButton').addEventListener('click', openQualityDialog);
$('qualityClose').addEventListener('click', closeQualityDialog);
$('qualityDialog').addEventListener('click', (event) => { if (event.target === $('qualityDialog')) closeQualityDialog(); });
$('qualityItems').addEventListener('click', (event) => {
  const button = event.target.closest('[data-quality-index]');
  if (!button) return;
  const index = Number(button.dataset.qualityIndex);
  closeQualityDialog();
  jumpToSegment(index);
});
$('paperSummaryButton').addEventListener('click', openPaperSummaryDialog);
$('paperSummaryClose').addEventListener('click', closePaperSummaryDialog);
$('paperSummaryCancel').addEventListener('click', closePaperSummaryDialog);
$('paperSummaryForm').addEventListener('submit', savePaperSummary);
$('paperTags').addEventListener('input', renderPaperTagPreview);
$('paperSummaryText').addEventListener('input', updatePaperSummaryLength);
$('paperSummaryTemplate').addEventListener('click', insertPaperSummaryTemplate);
$('paperSummaryDialog').addEventListener('click', (event) => { if (event.target === $('paperSummaryDialog')) closePaperSummaryDialog(); });
$('revisionClose').addEventListener('click', closeTranslationHistory);
$('revisionDialog').addEventListener('click', (event) => { if (event.target === $('revisionDialog')) closeTranslationHistory(); });
$('revisionItems').addEventListener('click', (event) => {
  const button = event.target.closest('[data-revision-restore]');
  if (button) restoreTranslationRevision(Number(button.dataset.revisionRestore), button);
});
$('results').addEventListener('input', (event) => {
  const translationEditor = event.target.closest('.translation-editor');
  if (translationEditor) {
    translationEditor.style.height = 'auto';
    translationEditor.style.height = `${Math.max(90, translationEditor.scrollHeight)}px`;
    return;
  }
  const editor = event.target.closest('.note-editor'); if (!editor) return;
  resizeNote(editor);
  const index = Number(editor.dataset.noteIndex); const status = editor.nextElementSibling;
  const item = state.segments.find((segment) => Number(segment.index) === index);
  if (item) item.note = editor.value;
  const clippedNote = document.querySelector(`.clip-card[data-clip-index="${index}"][data-clip-part="note"] .clip-card-body p`);
  if (clippedNote) clippedNote.textContent = editor.value || '（メモは未入力です）';
  clearTimeout(noteTimers.get(index)); status.textContent = '保存中…'; status.className = 'note-status saving';
  noteTimers.set(index, setTimeout(() => saveNote(index, editor.value, status), 650));
});
$('results').addEventListener('click', (event) => {
  const button = event.target.closest('.bookmark-toggle');
  if (button) toggleBookmark(button);
});
$('readerLayout').addEventListener('click', (event) => {
  const translationButton = event.target.closest('[data-translation-action]');
  if (translationButton) {
    const index = Number(translationButton.dataset.translationIndex);
    const action = translationButton.dataset.translationAction;
    if (action === 'edit') beginTranslationEdit(index);
    else if (action === 'save') submitTranslationEdit(index, translationButton);
    else if (action === 'cancel') rerenderSegment(index);
    else if (action === 'retranslate') retranslateSegment(index, translationButton);
    else if (action === 'history') openTranslationHistory(index);
    return;
  }
  const clipButton = event.target.closest('.clip-toggle');
  if (clipButton) { toggleClip(clipButton); return; }
  const reference = event.target.closest('.reference-link');
  if (reference) { jumpToReference(Number(reference.dataset.referenceIndex)); return; }
  const target = event.target.closest('[data-nav-index]');
  if (target) jumpToSegment(Number(target.dataset.navIndex));
});
$('readerSearch').addEventListener('input', (event) => {
  const query = event.target.value.trim().toLocaleLowerCase();
  document.querySelectorAll('.reader-row').forEach((row) => {
    const editor = row.querySelector('.note-editor');
    const searchable = `${row.querySelector('.source-pane')?.textContent || ''} ${row.querySelector('.translation-pane')?.textContent || ''} ${editor?.value || ''}`.toLocaleLowerCase();
    row.classList.toggle('filtered', Boolean(query) && !searchable.includes(query));
  });
});
window.addEventListener('scroll', scheduleReadingPositionSave, {passive:true});
window.addEventListener('beforeunload', () => {
  if (!state.jobId || !state.resultsReady) return;
  const index = currentReadingIndex();
  if (index === state.lastSavedReadingIndex) return;
  navigator.sendBeacon(`/api/jobs/${state.jobId}/metadata`, new Blob([JSON.stringify({reading_index:index})], {type:'application/json'}));
});
document.addEventListener('keydown', (event) => {
  if (event.altKey && !event.ctrlKey && !event.metaKey && event.key.toLowerCase() === 'l') {
    event.preventDefault();
    openLibraryDialog();
  }
  if (event.altKey && !event.ctrlKey && !event.metaKey && event.key.toLowerCase() === 's' && state.resultsReady) {
    event.preventDefault();
    openPaperSummaryDialog();
  }
});

function resumeJob() {
  const requested = new URLSearchParams(location.search).get('job');
  const saved = requested || localStorage.getItem(JOB_STORAGE_KEY);
  if (!saved || !/^[a-f0-9]{32}$/.test(saved)) return;
  state.jobId = saved; state.resultsReady = false; localStorage.setItem(JOB_STORAGE_KEY, saved);
  $('step3').classList.remove('results-ready');
  showStep(3, 'auto'); $('progressBox').classList.remove('hidden');
  $('progressMessage').textContent = '保存済みの翻訳状態を確認しています…';
  pollJob();
}

updateEngine(); setInterval(updateEngine, 5000); resumeJob();
if (new URLSearchParams(location.search).get('library') === '1') openLibraryDialog();
