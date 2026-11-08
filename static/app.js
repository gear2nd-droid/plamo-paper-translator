const JOB_STORAGE_KEY = 'plamo-paper-translator.job-id';
const state = { documentId: null, jobId: null, poller: null, polling: false, pollFailures: 0 };
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
  const payload = {document_id: state.documentId, title: $('title').value, source_lang: $('sourceLang').value, target_lang: $('targetLang').value, form: $('form').value, context: $('context').value, glossary: $('glossary').value};
  showStep(3); $('progressBox').classList.remove('hidden'); $('resultActions').classList.add('hidden'); $('results').innerHTML = '';
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
  $('progressBox').classList.add('hidden'); $('resultActions').classList.remove('hidden');
  $('readerHeader').classList.remove('hidden');
  $('resultSubtitle').textContent = `${data.total}行の対訳が完成しました。Pは段落、Sは段落内の文番号です。`;
  ['Html','Md','Json'].forEach((format) => $(`export${format}`).href = `/api/jobs/${state.jobId}/export?format=${format.toLowerCase()}`);
  $('results').innerHTML = data.segments.map((item, position, list) => renderSegment(item, position, list)).join('');
  toast('翻訳が完了しました');
}

function renderSegment(item, position, list) {
  const kind = item.kind || 'sentence';
  const labels = {sentence:'文', heading:'見出し', equation:'数式', table:'表', figure:'図'};
  const translation = item.translation || `⚠ ${item.error || '未翻訳'}`;
  const imageItems = (item.assets || []).map((asset, number) => `<figure><a href="${escapeHtml(asset)}" target="_blank" rel="noreferrer"><img src="${escapeHtml(asset)}" alt="図 ${item.index + 1}-${number + 1}" loading="lazy"></a></figure>`).join('');
  const images = imageItems ? `<div class="figure-gallery">${imageItems}</div>` : '';
  const hasPosition = Number(item.paragraph) > 0;
  const isParagraphStart = hasPosition && (position === 0 || item.paragraph !== list[position - 1].paragraph);
  const unitPosition = hasPosition ? `P${item.paragraph} / S${item.sentence}` : '旧形式';
  return `<section class="reader-row kind-${escapeHtml(kind)}${isParagraphStart ? ' paragraph-start' : ''}" data-index="${item.index}">
    <div class="unit-meta"><b>${String(item.index + 1).padStart(3, '0')}</b><span>${escapeHtml(unitPosition)}</span><i>${labels[kind] || kind}</i></div>
    <article class="source-pane">${images}<p>${escapeHtml(item.source)}</p></article>
    <article class="translation-pane"><p>${escapeHtml(translation)}</p></article>
    <aside class="memo-pane"><textarea class="note-editor" data-note-index="${item.index}" aria-label="${item.index + 1}番のメモ" placeholder="この箇所へのメモ…">${escapeHtml(item.note || '')}</textarea><small class="note-status">${item.note ? '保存済み' : '未入力'}</small></aside>
  </section>`;
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
$('results').addEventListener('input', (event) => {
  const editor = event.target.closest('.note-editor'); if (!editor) return;
  const index = Number(editor.dataset.noteIndex); const status = editor.nextElementSibling;
  clearTimeout(noteTimers.get(index)); status.textContent = '保存中…'; status.className = 'note-status saving';
  noteTimers.set(index, setTimeout(() => saveNote(index, editor.value, status), 650));
});
$('readerSearch').addEventListener('input', (event) => {
  const query = event.target.value.trim().toLocaleLowerCase();
  document.querySelectorAll('.reader-row').forEach((row) => row.classList.toggle('filtered', Boolean(query) && !row.textContent.toLocaleLowerCase().includes(query)));
});

function resumeJob() {
  const requested = new URLSearchParams(location.search).get('job');
  const saved = requested || localStorage.getItem(JOB_STORAGE_KEY);
  if (!saved || !/^[a-f0-9]{32}$/.test(saved)) return;
  state.jobId = saved; localStorage.setItem(JOB_STORAGE_KEY, saved);
  showStep(3); $('progressBox').classList.remove('hidden'); $('resultActions').classList.add('hidden');
  $('progressMessage').textContent = '保存済みの翻訳状態を確認しています…';
  pollJob();
}

updateEngine(); setInterval(updateEngine, 5000); resumeJob();
