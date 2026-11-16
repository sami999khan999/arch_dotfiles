// youtube.js — next / previous video for the desktop's media controls (the bar's player card, its scroll,
// the media keys), which reach Chrome over MPRIS. Chrome offers Next / Previous there only when the page sets
// Media Session handlers for them, and YouTube sets none for a single video (it clears them, even). So this runs
// in the page (world MAIN, before YouTube's own scripts) and stands in wherever YouTube leaves one empty:
// next is YouTube's own next button (the playlist's next, or Up next); previous is the playlist's previous
// button, else back to the start once past 3 s, else the video before (the tab's history).
(() => {
  const ms = navigator.mediaSession;
  if (!ms || !ms.setActionHandler) return;
  const set = ms.setActionHandler.bind(ms);

  const shown = (sel) => {
    const b = document.querySelector(sel);
    return b && b.offsetParent !== null && getComputedStyle(b).display !== "none" ? b : null;
  };
  const ours = {
    nexttrack: () => shown(".ytp-next-button")?.click(),
    previoustrack: () => {
      const prev = shown(".ytp-prev-button");
      const video = document.querySelector("video");
      if (prev) prev.click();
      else if (video && video.currentTime > 3) video.currentTime = 0;
      else history.back();
    },
  };

  // YouTube's own handler wins; an empty one (null) is ours instead
  ms.setActionHandler = (action, handler) => set(action, handler || ours[action] || null);
  for (const action of Object.keys(ours)) set(action, ours[action]);
})();
