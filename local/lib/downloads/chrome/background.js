// background.js — hands each of Chrome's downloads to the download manager (local/lib/downloads), which
// fetches it with many connections at once. Chrome's name for the file is known once the server has answered
// (onDeterminingFilename): the link, that name, the page it came from and the site's cookies go to the native
// host; if the manager took it, Chrome's own download is cancelled and taken off its list. If the manager isn't
// running (or says no), Chrome just keeps downloading. Packed per PC by `downloads enable`; a change here needs
// the version in manifest.json raised, or Chrome keeps the old one.
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
