const JOB_STORAGE_KEY = 'plamo-paper-translator.job-id';
const MAX_CLIPS = 3;
const CLIP_PARTS = {
  source: {key:'E', label:'英語'},
  translation: {key:'J', label:'日本語'},
  note: {key:'M', label:'メモ'},
};
const REFERENCE_PATTERN = /\b(?:fig(?:ure)?s?\.?\s*\d+(?:\s*[-–]\s*\d+)?|eq(?:uation)?s?\.?\s*\(?\d+\)?|tables?\s+\d+)|(?:図|式|表)\s*\(?\d+\)?/gi;
const state = { documentId: null, jobId: null, poller: null, polling: false, pollFailures: 0, segments: [], tocObserver: null, referenceTargets: new Map(), referenceHistory: [], resultsReady: false, paperTags: [], paperSummary: '', paperSummaryScrollY: 0 };
const noteTimers = new Map();
const $ = (id) => document.getElementById(id);

function toast(message) {
  const node = $('toast'); node.textContent = message; node.classList.add('show');
  setTimeout(() => node.classList.remove('show'), 3200);
}

function showStep(number) {
  document.querySelectorAll('.panel').forEach((node, i) => node.classList.toggle('active', i === number - 1));
  document.querySelectorAll('.step').forEach((node, i) => node.classList.toggle('active', i === number - 1));
  document.querySelector('.workspace').classList.toggle('reading', number === 3);
  updateReaderToolbar();
  window.scrollTo({top: document.querySelector('.workspace').offsetTop - 90, behavior: 'smooth'});
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
    $('uploadState').innerHTML = `<div><b>${escapeHtml(data.filename)}</b><p>本文の抽出が完了しました</p></div><div class="document-stats"><span>${data.pages}ページ</span><span>${data.segment_count}行</span><span>${data.characters.toLocaleString()}文字</span></div>`;
    $('toSettings').disabled = false; toast('論文を読み込みました');
  } catch (error) { $('uploadState').innerHTML = `<div><b>読み込めませんでした</b><p>${escapeHtml(error.message)}</p></div>`; toast(error.message); }
}

async function startJob() {
  if (!state.documentId) return toast('先に論文を読み込んでください');
  const payload = {document_id: state.documentId, title: $('title').value, segmentation_mode: document.querySelector('input[name="segmentationMode"]:checked').value, source_lang: $('sourceLang').value, target_lang: $('targetLang').value, form: $('form').value, context: $('context').value, glossary: $('glossary').value};
  state.resultsReady = false; state.segments = []; state.paperTags = []; state.paperSummary = '';
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
  const loadedSegments = (data.segments || []).map((item) => ({...item, clips:itemClips(item)}));
  const referenceLayout = prepareReferenceLayout(loadedSegments);
  state.segments = referenceLayout.segments;
  state.referenceTargets = referenceLayout.targets;
  state.referenceHistory = [];
  state.paperTags = Array.isArray(data.tags) ? data.tags.filter((tag) => typeof tag === 'string') : [];
  state.paperSummary = String(data.paper_summary || '');
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
  const translationContent = equationImage ? `${images}<details class="equation-text"><summary>抽出テキスト</summary><p>${translationText}</p></details>` : `<p>${translationText}</p>`;
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
  for (const match of text.matchAll(REFERENCE_PATTERN)) {
    rendered += escapeHtml(text.slice(lastIndex, match.index));
    const identity = referenceIdentity(match[0]);
    const target = identity ? state.referenceTargets.get(referenceKey(identity.type, identity.number)) : undefined;
    if (Number.isInteger(target) && target !== Number(currentIndex)) {
      rendered += `<button type="button" class="reference-link" data-reference-index="${target}" title="参照先へ移動">${escapeHtml(match[0])}</button>`;
    } else {
      rendered += escapeHtml(match[0]);
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

function updateReaderToolbar() {
  const stepThreeActive = $('step3').classList.contains('active');
  const visible = state.resultsReady && stepThreeActive;
  $('resultActions').classList.toggle('hidden', !visible);
  $('paperSummaryButton').classList.toggle('hidden', !visible);
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
    const response = await fetch(`/api/jobs/${state.jobId}/summary`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tags, summary})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || '論文のまとめを保存できません');
    state.paperTags = data.tags || [];
    state.paperSummary = data.summary || '';
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
$('toSettings').addEventListener('click', () => showStep(2));
document.querySelectorAll('.back').forEach((node) => node.addEventListener('click', () => showStep(Number(node.dataset.target))));
document.querySelectorAll('.step').forEach((node) => node.addEventListener('click', () => { const n=Number(node.dataset.step); if(n===1 || (n===2 && state.documentId) || (n===3 && state.jobId)) showStep(n); }));
$('targetLang').addEventListener('change', () => $('form').closest('label').style.display = $('targetLang').value === 'ja' ? '' : 'none');
$('startTranslation').addEventListener('click', startJob);
$('cancelJob').addEventListener('click', async () => { if(state.jobId) await fetch(`/api/jobs/${state.jobId}/cancel`, {method:'POST'}); });
$('newPaper').addEventListener('click', () => { localStorage.removeItem(JOB_STORAGE_KEY); location.href = '/'; });
$('referenceBack').addEventListener('click', returnFromReference);
$('paperSummaryButton').addEventListener('click', openPaperSummaryDialog);
$('paperSummaryClose').addEventListener('click', closePaperSummaryDialog);
$('paperSummaryCancel').addEventListener('click', closePaperSummaryDialog);
$('paperSummaryForm').addEventListener('submit', savePaperSummary);
$('paperTags').addEventListener('input', renderPaperTagPreview);
$('paperSummaryText').addEventListener('input', updatePaperSummaryLength);
$('paperSummaryDialog').addEventListener('click', (event) => { if (event.target === $('paperSummaryDialog')) closePaperSummaryDialog(); });
$('results').addEventListener('input', (event) => {
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

function resumeJob() {
  const requested = new URLSearchParams(location.search).get('job');
  const saved = requested || localStorage.getItem(JOB_STORAGE_KEY);
  if (!saved || !/^[a-f0-9]{32}$/.test(saved)) return;
  state.jobId = saved; state.resultsReady = false; localStorage.setItem(JOB_STORAGE_KEY, saved);
  $('step3').classList.remove('results-ready');
  showStep(3); $('progressBox').classList.remove('hidden');
  $('progressMessage').textContent = '保存済みの翻訳状態を確認しています…';
  pollJob();
}

updateEngine(); setInterval(updateEngine, 5000); resumeJob();
