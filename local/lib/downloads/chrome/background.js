// background.js — hands each of Chrome's downloads to the download manager (local/lib/downloads), which
// fetches it with many connections at once. Chrome's name for the file is known once the server has answered
// (onDeterminingFilename): the link, that name, the page it came from and the site's cookies go to the native
// host; if the manager took it, Chrome's own download is cancelled and taken off its list. If the manager isn't
// running (or says no), Chrome just keeps downloading.
// It also tells the host which tabs are open (each window's, incognito left out) whenever they change, at most
// every 2 s: the Setups panel (local/lib/panels/setupsgui.py) records them with the rest of the desktop.
// And a tab opened in the background (a Setups restore, a saved tab group, Chrome's own restore, a link opened
// in a new tab) stays dormant — discarded, no page running — until you go to it, when Chrome loads it.
// Packed per PC by `downloads enable`; a change here needs the version in manifest.json raised, or Chrome keeps
// the old one.
const HOST = "sami.downloads";

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  const url = item.finalUrl || item.url;
  // blob: / data: exist only inside the page; a download another extension started is that one's business
  if (!/^https?:/i.test(url) || item.byExtensionId) {
    suggest();
    return;
  }
  handOver(item, url).then((taken) => {
    if (taken) {
      chrome.downloads.cancel(item.id, () => chrome.downloads.erase({ id: item.id }));
    }
    suggest();
  });
  return true; // suggest() comes later
});

async function handOver(item, url) {
  try {
    const cookies = await chrome.cookies.getAll({ url });
    const reply = await chrome.runtime.sendNativeMessage(HOST, {
      url,
      name: item.filename.split("/").pop(),
      referrer: item.referrer || "",
      cookie: cookies.map((c) => `${c.name}=${c.value}`).join("; "),
      agent: navigator.userAgent,
      size: item.totalBytes > 0 ? item.totalBytes : 0,
    });
    return Boolean(reply && reply.ok);
  } catch (e) {
    return false; // no host, or it failed: Chrome keeps the download
  }
}

// ---- open tabs, for Setups ----------------------------------------------------------------------------------
let tabsTimer = null;

function tabsChanged() {
  clearTimeout(tabsTimer);
  tabsTimer = setTimeout(sendTabs, 2000);
}

async function sendTabs() {
  try {
    const wins = await chrome.windows.getAll({ populate: true, windowTypes: ["normal"] });
    const windows = wins.filter((w) => !w.incognito).map((w) => {
      const active = w.tabs.find((t) => t.active);
      return {
        focused: w.focused,
        title: active ? active.title : "",
        tabs: w.tabs.map((t) => ({ url: t.pendingUrl || t.url, title: t.title, pinned: t.pinned, active: t.active })),
      };
    });
    await chrome.runtime.sendNativeMessage(HOST, { op: "tabs", windows });
  } catch (e) {
    // no host: nothing to record
  }
}

chrome.tabs.onCreated.addListener(tabsChanged);
chrome.tabs.onRemoved.addListener(tabsChanged);
chrome.tabs.onMoved.addListener(tabsChanged);
chrome.tabs.onAttached.addListener(tabsChanged);
chrome.tabs.onActivated.addListener(tabsChanged);   // the window's title (how it's matched to its workspace)
chrome.tabs.onUpdated.addListener((_id, change) => {
  if (change.url || change.title) tabsChanged();
});
chrome.windows.onCreated.addListener(tabsChanged);
chrome.windows.onRemoved.addListener(tabsChanged);
chrome.runtime.onStartup.addListener(tabsChanged);
chrome.runtime.onInstalled.addListener(tabsChanged);

// ---- background tabs dormant until visited ------------------------------------------------------------------
// A tab can only be discarded once its address is committed (discarding before that leaves it blank), so a new
// background tab waits here for its first committed web address. about:blank and the like are left alone: a
// page that opened one may be writing into it.
const waking = new Set();

function sleepTab(tab) {
  if (tab.active || tab.discarded || !/^https?:/i.test(tab.url || "")) return false;
  chrome.tabs.discard(tab.id).catch(() => {}); // gone, or Chrome won't (e.g. it's playing): it just loads
  return true;
}

chrome.tabs.onCreated.addListener((tab) => {
  if (!tab.active && !sleepTab(tab)) waking.add(tab.id);
});
chrome.tabs.onUpdated.addListener((id, change, tab) => {
  if (waking.has(id) && change.url && (tab.active || sleepTab(tab))) waking.delete(id);
});
chrome.tabs.onActivated.addListener(({ tabId }) => waking.delete(tabId));
chrome.tabs.onRemoved.addListener((id) => waking.delete(id));
// Chrome's own restore may make its tabs before this worker is up: put every background tab to sleep
chrome.runtime.onStartup.addListener(async () => {
  for (const tab of await chrome.tabs.query({ active: false, discarded: false })) sleepTab(tab);
});
