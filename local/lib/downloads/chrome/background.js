// background.js — hands each of Chrome's downloads to the download manager (local/lib/downloads), which
// fetches it with many connections at once. Chrome's name for the file is known once the server has answered
// (onDeterminingFilename): the link, that name, the page it came from and the site's cookies go to the native
// host; if the manager took it, Chrome's own download is cancelled and taken off its list. If the manager isn't
// running (or says no), Chrome just keeps downloading.
// It also tells the host which tabs are open (each window's, incognito left out) whenever they change, at most
// every 2 s, with their tab groups: the Setups panel (local/lib/panels/setupsgui.py) records them with the rest of
// the desktop. When Setups is opening a window again, the host's reply says which of its tabs to group and pin
// (Chrome's command line, which opened it, can't).
// And a tab opened in the background (a Setups restore, a saved tab group, Chrome's own restore, a link opened
// in a new tab) stays dormant — discarded, no page running — until you go to it, when Chrome loads it.
// Packed per PC by `downloads enable`; a change here needs the version in manifest.json raised, or Chrome keeps
// the old one. Its update_url is what Chrome checks for updates once it's installed (the policy's URL only
// installs it): without it, Chrome never updates it.
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
    const windows = [];
    for (const w of wins.filter((w) => !w.incognito)) {
      const active = w.tabs.find((t) => t.active);
      const groups = await chrome.tabGroups.query({ windowId: w.id });
      const at = new Map(groups.map((g, i) => [g.id, i]));
      windows.push({
        id: w.id,
        focused: w.focused,
        title: active ? active.title : "",
        tabs: w.tabs.map((t) => ({
          url: t.pendingUrl || t.url, title: t.title, pinned: t.pinned, active: t.active,
          group: at.has(t.groupId) ? at.get(t.groupId) : -1,
        })),
        groups: groups.map((g) => ({ title: g.title || "", color: g.color, collapsed: g.collapsed })),
      });
    }
    const reply = await chrome.runtime.sendNativeMessage(HOST, { op: "tabs", windows });
    if (reply && reply.regroup) await regroup(reply.regroup);
  } catch (e) {
    console.warn("tab report not sent:", e); // no host, or it failed: nothing recorded
  }
}

// [{window, pinned: [tab index], groups: [{tabs: [tab index], title, color, collapsed}]}]: a window Setups opened
async function regroup(list) {
  for (const r of list) {
    const win = await chrome.windows.get(r.window, { populate: true }).catch(() => null);
    if (!win) continue;
    const ids = win.tabs.map((t) => t.id);
    for (const i of r.pinned || []) {
      if (ids[i] !== undefined) await chrome.tabs.update(ids[i], { pinned: true }).catch(() => {});
    }
    for (const g of r.groups || []) {
      const tabIds = g.tabs.map((i) => ids[i]).filter((id) => id !== undefined);
      if (!tabIds.length) continue;
      try {
        const id = await chrome.tabs.group({ tabIds, createProperties: { windowId: win.id } });
        await chrome.tabGroups.update(id, { title: g.title || "", color: g.color || "grey" });
        if (g.collapsed) await chrome.tabGroups.update(id, { collapsed: true }); // not if it holds the active tab
      } catch (e) {
        // a tab gone meanwhile: the rest still get theirs
      }
    }
  }
}

chrome.tabs.onCreated.addListener(tabsChanged);
chrome.tabs.onRemoved.addListener(tabsChanged);
chrome.tabs.onMoved.addListener(tabsChanged);
chrome.tabs.onAttached.addListener(tabsChanged);
chrome.tabs.onActivated.addListener(tabsChanged);   // the window's title (how it's matched to its workspace)
chrome.tabs.onUpdated.addListener((_id, change) => {
  if (change.url || change.title || "groupId" in change || "pinned" in change) tabsChanged();
});
for (const ev of ["onCreated", "onUpdated", "onRemoved", "onMoved"]) chrome.tabGroups[ev].addListener(tabsChanged);
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
