const APP_URL = 'http://127.0.0.1:7865/';
const NATIVE_HOST = 'jp.gear2.plamo_paper_translator';

async function appIsAvailable() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1500);
  try {
    // /api/ping does not wait for the translation engine, unlike /api/status.
    const response = await fetch(`${APP_URL}api/ping`, {cache: 'no-store', signal: controller.signal});
    if (!response.ok) return false;
    const payload = await response.json();
    return payload.app === 'plamo-paper-translator';
  } catch (_) {
    return false;
  } finally {
    clearTimeout(timeout);
  }
}

function callNativeHost(action) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(NATIVE_HOST, {action}, (response) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(response || {ok: false, error: '起動ホストから応答がありません。'});
    });
  });
}

async function openAppTab(notice = '') {
  const url = notice ? `${APP_URL}?notice=${encodeURIComponent(notice)}` : APP_URL;
  const existing = await chrome.tabs.query({url: `${APP_URL}*`});
  if (existing.length) {
    const tab = existing[0];
    await chrome.tabs.update(tab.id, {active: true, url});
    if (tab.windowId !== undefined) await chrome.windows.update(tab.windowId, {focused: true});
    return;
  }
  await chrome.tabs.create({url});
}

let busy = false;

async function restartAndOpen() {
  if (busy) return;
  busy = true;
  await chrome.action.setBadgeBackgroundColor({color: '#1a56b8'});
  await chrome.action.setBadgeText({text: '…'});
  try {
    let response;
    try {
      // Always restart so a replaced app or model is picked up.
      response = await callNativeHost('restart');
    } catch (error) {
      // Without the native host we can still open an app that is running.
      if (!(await appIsAvailable())) throw error;
      await openAppTab('restart-failed');
      await chrome.action.setBadgeText({text: ''});
      return;
    }
    if (!response.ok) throw new Error(response.error || 'アプリを起動できませんでした。');
    await openAppTab(response.busy ? 'restart-busy' : response.restarted ? 'restarted' : '');
    await chrome.action.setBadgeText({text: ''});
  } catch (error) {
    await chrome.action.setBadgeBackgroundColor({color: '#b84c3d'});
    await chrome.action.setBadgeText({text: '!'});
    const setupUrl = new URL(chrome.runtime.getURL('setup.html'));
    setupUrl.searchParams.set('error', error.message || String(error));
    await chrome.tabs.create({url: setupUrl.toString()});
  } finally {
    busy = false;
  }
}

chrome.action.onClicked.addListener(restartAndOpen);
chrome.commands.onCommand.addListener((command) => {
  if (command === 'open-plamo') restartAndOpen();
});
chrome.runtime.onInstalled.addListener(({reason}) => {
  if (reason === 'install') chrome.tabs.create({url: chrome.runtime.getURL('setup.html')});
});
