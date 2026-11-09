const APP_URL = 'http://127.0.0.1:7865/';
const NATIVE_HOST = 'jp.gear2.plamo_paper_translator';

async function appIsAvailable() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1200);
  try {
    const response = await fetch(`${APP_URL}api/status`, {cache: 'no-store', signal: controller.signal});
    if (!response.ok) return false;
    const payload = await response.json();
    return typeof payload.engine === 'string';
  } catch (_) {
    return false;
  } finally {
    clearTimeout(timeout);
  }
}

function launchNativeHost() {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(NATIVE_HOST, {action: 'launch'}, (response) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(response || {ok: false, error: '起動ホストから応答がありません。'});
    });
  });
}

async function openAppTab() {
  const existing = await chrome.tabs.query({url: `${APP_URL}*`});
  if (existing.length) {
    const tab = existing[0];
    await chrome.tabs.update(tab.id, {active: true, url: APP_URL});
    if (tab.windowId !== undefined) await chrome.windows.update(tab.windowId, {focused: true});
    return;
  }
  await chrome.tabs.create({url: APP_URL});
}

async function launchAndOpen() {
  await chrome.action.setBadgeBackgroundColor({color: '#174d36'});
  await chrome.action.setBadgeText({text: '…'});
  try {
    if (!(await appIsAvailable())) {
      const response = await launchNativeHost();
      if (!response.ok) throw new Error(response.error || 'アプリを起動できませんでした。');
    }
    await openAppTab();
    await chrome.action.setBadgeText({text: ''});
  } catch (error) {
    await chrome.action.setBadgeBackgroundColor({color: '#b84c3d'});
    await chrome.action.setBadgeText({text: '!'});
    const setupUrl = new URL(chrome.runtime.getURL('setup.html'));
    setupUrl.searchParams.set('error', error.message || String(error));
    await chrome.tabs.create({url: setupUrl.toString()});
  }
}

chrome.action.onClicked.addListener(launchAndOpen);
chrome.commands.onCommand.addListener((command) => {
  if (command === 'open-plamo') launchAndOpen();
});
chrome.runtime.onInstalled.addListener(({reason}) => {
  if (reason === 'install') chrome.tabs.create({url: chrome.runtime.getURL('setup.html')});
});
