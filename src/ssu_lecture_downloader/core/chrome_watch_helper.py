"""The page helper injected into the Chrome playback window (every frame of the lecture page).

It only observes. It reports the video or audio state and where the player's start button and
the "resume?" 예 button are through the `ssuWatchSignal` binding (Runtime.addBinding), and answers
`window.__ssuWatchLocate(what)` so the driver can press those buttons with trusted mouse
events. It never calls `video.play()`, never changes the playback rate, never seeks and never
touches visibility: the player's own flow (start button, resume prompt, short intro, lecture)
runs untouched, and Chrome decides what a background or covered window may play.

Kept as a Python string (not a data file) so the packaged backend needs no extra resources.
"""

from __future__ import annotations

BINDING_NAME = "ssuWatchSignal"

HELPER_JS = r"""
(() => {
  if (window.__ssuWatchHelper) return;
  window.__ssuWatchHelper = true;

  const SHORT_INTRO_MAX_SECONDS = 10;
  const REPORT_INTERVAL_MS = 1000;
  // Player prompts answered the way a person would: (question text, button to press).
  //  - resume: "이전에 시청했던 07:29부터 이어서 보시겠습니까?" -> 예
  //  - progress tracking: "본 콘텐츠의 진도체크를 시작합니다. 실행중인 다른 콘텐츠의
  //    진도체크는 중단됩니다." -> 확인
  const PROMPTS = [
    { text: '이어서 보시겠습니까', button: '예' },
    { text: '진도체크를 시작합니다', button: '확인' },
  ];
  const FRONT_SELECTOR = '#front-screen .vc-front-screen-play-btn, .vc-front-screen-play-btn';

  const isTop = (() => { try { return window.top === window; } catch (_) { return false; } })();
  const host = (() => { try { return window.location.hostname || ''; } catch (_) { return ''; } })();

  const send = (payload) => {
    try {
      if (typeof window.ssuWatchSignal === 'function') {
        window.ssuWatchSignal(JSON.stringify(Object.assign({ top: isTop, host: host }, payload)));
      }
    } catch (_) {}
  };

  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const isShown = (element) => {
    try {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    } catch (_) {
      return false;
    }
  };
  const duration = (video) => (video && Number.isFinite(video.duration) ? video.duration : 0);
  const isMain = (video) => duration(video) > SHORT_INTRO_MAX_SECONDS;

  let lastAudio = null;
  const findMedia = () => {
    const candidates = Array.from(document.querySelectorAll('video,audio'));
    const videos = candidates.filter((el) => el.localName === 'video');
    const usable = videos.filter((video) => {
      const rect = video.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && (video.currentSrc || video.src || video.readyState > 0);
    });
    const legacy = usable.find(isMain) || usable[0] || videos.find(isMain) || videos[0] || null;
    if (legacy && isMain(legacy)) return legacy;
    // No main-length video: fall back to a main-length <audio> (some lectures play slides on a canvas with the sound in <audio>).
    const audios = candidates.filter((el) => el.localName === 'audio' && isMain(el));
    const playing = audios.find((el) => !el.paused && !el.ended);
    if (playing) { lastAudio = playing; return playing; }
    if (lastAudio && lastAudio.isConnected !== false && audios.includes(lastAudio)) return lastAudio;
    return audios[0] || legacy;
  };

  const findFront = () => {
    const button = document.querySelector(FRONT_SELECTOR);
    return button && isShown(button) ? button : null;
  };

  // A control labelled exactly like the prompt's button, inside the dialog with its question.
  const pickButton = (candidates, prompt) => {
    for (const control of candidates) {
      if (normalize(control.innerText || control.textContent || control.value) !== prompt.button || !isShown(control)) continue;
      let scope = control.parentElement;
      for (let depth = 0; scope && depth < 6; depth += 1, scope = scope.parentElement) {
        if (normalize(scope.textContent).includes(prompt.text)) return control;
      }
    }
    return null;
  };
  const findYes = () => {
    const body = document.body;
    if (!body) return null;
    const text = normalize(body.textContent);
    for (const prompt of PROMPTS) {
      if (!text.includes(prompt.text)) continue;
      const buttons = Array.from(document.querySelectorAll('button,[role="button"],a,input[type="button"],input[type="submit"]'));
      const found = pickButton(buttons, prompt)
        || pickButton(Array.from(document.querySelectorAll('div,span,li,p')).filter((element) => !element.children || element.children.length === 0), prompt);
      if (found) return found;
    }
    return null;
  };

  // Where the driver should click: the element's box in this frame's viewport, after a
  // scroll like a person would do to see it.
  window.__ssuWatchLocate = (what) => {
    const element = what === 'yes' ? findYes() : what === 'front' ? findFront() : null;
    if (!element) return null;
    try { element.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_) {}
    const rect = element.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) return null;
    return { x: rect.left, y: rect.top, w: rect.width, h: rect.height, vw: window.innerWidth, vh: window.innerHeight };
  };

  let endedSent = false;
  const sendEnded = (video) => {
    if (endedSent || !isMain(video)) return;
    endedSent = true;
    send({ kind: 'ended', time: video.currentTime, duration: video.duration });
  };

  // Diagnostics only: keep the selected video and all playback decisions unchanged.
  const countMedia = () => {
    try {
      const media = Array.from(document.querySelectorAll('video,audio'));
      const videos = new Set(media.filter((el) => el.localName === 'video'));
      const audios = new Set(media.filter((el) => el.localName === 'audio'));
      let visited = 0;
      let scanCapped = false;
      const walk = (first, depth) => {
        for (let element = first; element; element = element.nextElementSibling) {
          if (visited >= 2000) { scanCapped = true; return; }
          visited += 1;
          if (depth > 0 && element.localName === 'video') videos.add(element);
          if (depth > 0 && element.localName === 'audio') audios.add(element);
          const shadow = element.shadowRoot;
          if (shadow) {
            if (depth >= 4) scanCapped = true;
            else walk(shadow.firstElementChild, depth + 1);
          }
          walk(element.firstElementChild, depth);
        }
      };
      walk(document.documentElement, 0);
      let playingCount = 0;
      let playingLong = 0;
      for (const video of videos) {
        if (!video.paused && !video.ended) {
          playingCount += 1;
          // Uses the helper's existing 10 s rule, not the driver's LMS length rule.
          if (isMain(video)) playingLong += 1;
        }
      }
      const playingAudio = Array.from(audios).filter((el) => !el.paused && !el.ended).length;
      return { videoCount: videos.size, playingCount, playingLong, scanCapped, audioCount: audios.size, playingAudio };
    } catch (_) {
      return { videoCount: null, playingCount: null, playingLong: null, scanCapped: null, audioCount: null, playingAudio: null };
    }
  };

  const attached = new WeakSet();
  // The driver reads this itself (Runtime.evaluate), so progress does not depend on this page's
  // timers, which Chrome slows down in pages that are not on screen.
  const snapshot = () => {
    const video = findMedia();
    if (video) {
      if (!attached.has(video)) {
        attached.add(video);
        video.addEventListener('ended', () => sendEnded(video));
      }
      // No element muting: Chrome pauses muted videos in a minimized window. The dedicated
      // Chrome runs with --mute-audio instead, so nothing is heard and playback is unchanged.
      if (video.ended) sendEnded(video);
    }
    return {
      ...countMedia(),
      kind: 'state',
      top: isTop,
      host: host,
      front: Boolean(findFront()),
      resume: Boolean(findYes()),
      video: video ? {
        tag: video.localName === 'audio' ? 'audio' : 'video',
        time: Number.isFinite(video.currentTime) ? video.currentTime : 0,
        duration: duration(video),
        paused: Boolean(video.paused),
        ended: Boolean(video.ended),
        main: isMain(video),
        ready: video.readyState,
      } : null,
    };
  };
  window.__ssuWatchState = () => { try { return snapshot(); } catch (_) { return null; } };
  const report = () => {
    const state = window.__ssuWatchState();
    if (state) send(state);
  };
  setInterval(report, REPORT_INTERVAL_MS);
  report();
})();
"""
