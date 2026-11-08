const state = { documentId: null, jobId: null, poller: null };
const $ = (id) => document.getElementById(id);

function toast(message) {
  const node = $('toast'); node.textContent = message; node.classList.add('show');
  setTimeout(() => node.classList.remove('show'), 3200);
}

function showStep(number) {
  document.querySelectorAll('.panel').forEach((node, i) => node.classList.toggle('active', i === number - 1));
  document.querySelectorAll('.step').forEach((node, i) => node.classList.toggle('active', i === number - 1));
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
    $('uploadState').innerHTML = `<div><b>${escapeHtml(data.filename)}</b><p>本文の抽出が完了しました</p></div><div class="document-stats"><span>${data.pages}ページ</span><span>${data.segment_count}区間</span><span>${data.characters.toLocaleString()}文字</span></div>`;
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
    state.jobId = data.id; pollJob(); state.poller = setInterval(pollJob, 1200);
  } catch (error) { $('progressMessage').textContent = error.message; toast(error.message); }
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const response = await fetch(`/api/jobs/${state.jobId}`); const data = await response.json();
    if (!response.ok) throw new Error(data.error || '状態を取得できません');
    $('progressMessage').textContent = data.message; $('progressPercent').textContent = `${Math.round(data.progress || 0)}%`; $('progressBar').style.width = `${data.progress || 0}%`;
    if (['completed','completed_with_errors','failed','cancelled'].includes(data.status)) {
      clearInterval(state.poller); state.poller = null;
      if (data.status.startsWith('completed')) showResults(data); else { $('resultSubtitle').textContent = data.message; toast(data.message); }
    }
  } catch (error) { clearInterval(state.poller); $('progressMessage').textContent = error.message; }
}

function showResults(data) {
  $('progressBox').classList.add('hidden'); $('resultActions').classList.remove('hidden');
  $('resultSubtitle').textContent = `${data.total}区間の対訳が完成しました。HTMLはブラウザからPDF印刷もできます。`;
  ['Html','Md','Json'].forEach((format) => $(`export${format}`).href = `/api/jobs/${state.jobId}/export?format=${format.toLowerCase()}`);
  $('results').innerHTML = data.segments.map((item) => `<section class="pair"><span class="page">PAGE ${item.page}</span><article><h3>ORIGINAL</h3><p>${escapeHtml(item.source)}</p></article><article><h3>TRANSLATION</h3><p>${escapeHtml(item.translation || `⚠ ${item.error || '未翻訳'}`)}</p></article></section>`).join('');
  toast('翻訳が完了しました');
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
$('newPaper').addEventListener('click', () => location.reload());
updateEngine(); setInterval(updateEngine, 5000);
