(() => {
  if (window.__SSU_WATCH_ASSIST_INSTALLED__) return;
  window.__SSU_WATCH_ASSIST_INSTALLED__ = true;

  const mode = 'sequential';
  const shouldEmitEnded = true;
  const frame = (() => {
    try { return window.top === window ? 'top' : 'child'; } catch (_) { return 'unknown'; }
  })();
  const host = (() => {
    try { return window.location.hostname || 'unknown'; } catch (_) { return 'unknown'; }
  })();
  let lastDebug = '';
  const debug = (stage, extra = {}) => {
    const payload = {
      stage,
      mode,
      frame,
      host,
      ready: document.readyState,
      videos: String(document.querySelectorAll('video').length),
      buttons: String(document.querySelectorAll('button,[role="button"],.vjs-big-play-button,.vjs-play-control').length),
      ...extra
    };
    const key = JSON.stringify(payload);
    if (key === lastDebug) return;
    lastDebug = key;
    const query = Object.entries(payload)
      .filter(([, value]) => value !== undefined && value !== null && String(value).length > 0)
      .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value)).slice(0, 180)}`)
      .join('&');
    try {
      if (window.top === window) {
        window.location.href = `ssu-watch://debug?${query}`;
      } else {
        window.top.postMessage({ __SSU_WATCH_DEBUG__: true, payload }, '*');
      }
    } catch (_) {}
  };
  try {
    if (window.top === window) {
      window.addEventListener('message', (event) => {
        if (!event.data || event.data.__SSU_WATCH_DEBUG__ !== true || !event.data.payload) return;
        const payload = { ...event.data.payload, frame: event.data.payload.frame || 'child' };
        const query = Object.entries(payload)
          .filter(([, value]) => value !== undefined && value !== null && String(value).length > 0)
          .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value)).slice(0, 180)}`)
          .join('&');
        try { window.location.href = `ssu-watch://debug?${query}`; } catch (_) {}
      });
    }
  } catch (_) {}

  const SHORT_INTRO_MAX_SECONDS = 10;
  const videoDuration = (video) => Number.isFinite(video && video.duration) ? video.duration : 0;
  const isLikelyMainVideo = (video) => videoDuration(video) > SHORT_INTRO_MAX_SECONDS;

  const notifyEnded = (video) => {
    if (!isLikelyMainVideo(video)) {
      debug('short-video-ended', { video: videoState(video) });
      return;
    }
    if (!shouldEmitEnded) {
      debug('main-video-ended', { video: videoState(video) });
      return;
    }
    debug('ended', { video: videoState(video) });
    try {
      window.location.href = 'ssu-watch://ended';
    } catch (_) {}
  };

  const videoState = (video) => {
    if (!video) return 'none';
    return [
      `paused=${video.paused}`,
      `ended=${video.ended}`,
      `muted=${video.muted}`,
      `ready=${video.readyState}`,
      `network=${video.networkState}`,
      `time=${Number.isFinite(video.currentTime) ? Math.round(video.currentTime) : 'na'}`,
      `duration=${Number.isFinite(video.duration) ? Math.round(video.duration) : 'na'}`
    ].join(',');
  };

  const findVideo = () => {
    const videos = Array.from(document.querySelectorAll('video'));
    return videos.find((video) => {
      const rect = video.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && (video.currentSrc || video.src || video.readyState > 0);
    }) || videos[0];
  };

  const attachVideo = (video) => {
    if (!video) return false;
    const attachmentKey = `${video.currentSrc || video.src || 'pending'}|${Math.round(videoDuration(video))}`;
    if (video.__SSU_WATCH_ASSIST_ATTACHED__ !== attachmentKey) {
      video.__SSU_WATCH_ASSIST_ATTACHED__ = attachmentKey;
      video.addEventListener('ended', () => notifyEnded(video), { once: true });
      debug('video-attached', { video: videoState(video), main: String(isLikelyMainVideo(video)) });
    }
    return true;
  };

  const tryPlayVideo = (video, attempt) => {
    if (!attachVideo(video)) return false;
    debug('video-found', { video: videoState(video) });
    if (!video.paused && !video.ended) return true;
    try {
      debug('play-attempt', { video: videoState(video), attempt });
      const result = video.play();
      if (result && typeof result.then === 'function') {
        result.then(() => debug('play-resolved', { video: videoState(video) })).catch((error) => {
          debug('play-rejected', {
            video: videoState(video),
            error: error && error.name ? error.name : 'unknown',
            message: error && error.message ? error.message : 'no-message'
          });
        });
      }
    } catch (error) {
      debug('play-threw', {
        video: videoState(video),
        error: error && error.name ? error.name : 'unknown',
        message: error && error.message ? error.message : 'no-message'
      });
    }
    return !video.paused && !video.ended;
  };

  const clickLikelyPlayControl = () => {
    const selectors = [
      '.vjs-big-play-button',
      '.vjs-play-control',
      '.play-button',
      '.btn-play',
      '[class*="play"]',
      'button[aria-label="Play"]',
      'button[title="Play"]',
      'button[aria-label="재생"]',
      'button[title="재생"]',
      '[role="button"][aria-label="Play"]',
      '[role="button"][aria-label="재생"]'
    ];
    for (const selector of selectors) {
      const button = document.querySelector(selector);
      if (button && typeof button.click === 'function') {
        try {
          debug('button-click-attempt', { selector });
          button.click();
          debug('button-clicked', { selector });
          return true;
        } catch (error) {
          debug('button-click-error', {
            selector,
            error: error && error.name ? error.name : 'unknown',
            message: error && error.message ? error.message : 'no-message'
          });
        }
      }
    }
    return false;
  };

  debug('installed');
  let attempts = 0;
  const tick = () => {
    attempts += 1;
    const video = findVideo();
    const playing = tryPlayVideo(video, String(attempts));
    if (!playing) clickLikelyPlayControl();
    if (!video && (attempts === 1 || attempts % 10 === 0)) debug('no-video', { attempt: String(attempts) });
    const current = findVideo();
    const done = current && !current.paused && !current.ended && isLikelyMainVideo(current);
    if (done) debug('playing', { video: videoState(current), attempt: String(attempts), main: 'true' });
    return done;
  };

  const timer = setInterval(() => {
    if (tick() || attempts >= 120) {
      if (attempts >= 120) debug('gave-up', { attempt: String(attempts), video: videoState(findVideo()) });
      clearInterval(timer);
    }
  }, 500);
  tick();
})();
