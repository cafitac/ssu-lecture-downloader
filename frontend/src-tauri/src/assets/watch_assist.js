(() => {
  if (window.__SSU_WATCH_ASSIST_INSTALLED__) return;
  window.__SSU_WATCH_ASSIST_INSTALLED__ = true;

  // Sequential-watch helper for the small playback window. It only does what a person
  // would do in front of the player: mute it, press play, answer the "resume?" dialog with
  // "yes", and tell the app how far the video is. It never changes the playback speed, never
  // seeks, and never pretends the page is visible or focused: if the window is minimized or
  // covered the player stops by itself, and the app keeps the window in a visible corner.
  const mode = 'sequential';
  const shouldEmitEnded = true;
  const isTop = (() => {
    try { return window.top === window; } catch (_) { return false; }
  })();
  const frame = isTop ? 'top' : 'child';
  const host = (() => {
    try { return window.location.hostname || 'unknown'; } catch (_) { return 'unknown'; }
  })();

  const SHORT_INTRO_MAX_SECONDS = 10;
  const PROGRESS_INTERVAL_MS = 5000;
  const HEARTBEAT_INTERVAL_MS = 10000;
  const STALL_AFTER_MS = 60000;
  const FRAME_ALIVE_MS = 2500;
  const FAST_TICK_MS = 500;
  const SLOW_TICK_MS = 1000;
  const FAST_TICKS = 120;
  const PLAY_RETRY_MIN_MS = 500;
  const PLAY_RETRY_MAX_MS = 15000;
  const RESUME_DIALOG_TEXT = '이어서 보시겠습니까';
  const RESUME_YES_LABEL = '예';
  const RESUME_CLICK_COOLDOWN_MS = 2500;
  const RESUME_MAX_CLICKS = 6;
  const OUTBOX_GAP_MS = 150;
  const OUTBOX_MAX = 40;

  const URLS = {
    debug: 'ssu-watch://debug',
    progress: 'ssu-watch://progress',
    heartbeat: 'ssu-watch://heartbeat',
    ended: 'ssu-watch://ended',
    stalled: 'ssu-watch://stalled'
  };

  // ------------------------------------------------------------------ signalling
  // Signals travel as navigations to ssu-watch:// URLs that the app intercepts. The page can
  // only start one navigation at a time without replacing the previous one, so the top frame
  // sends them one by one; child frames hand theirs to the top frame with postMessage.

  const encodeQuery = (payload) => Object.entries(payload)
    .filter(([, value]) => value !== undefined && value !== null && String(value).length > 0)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value).slice(0, 180))}`)
    .join('&');

  const outbox = [];
  let pumping = false;
  const pump = () => {
    if (pumping) return;
    pumping = true;
    const step = () => {
      const next = outbox.shift();
      if (!next) { pumping = false; return; }
      try { window.location.href = `${URLS[next.kind]}?${encodeQuery(next.payload)}`; } catch (_) {}
      setTimeout(step, OUTBOX_GAP_MS);
    };
    step();
  };
  const enqueue = (kind, payload) => {
    if (!URLS[kind]) return;
    if (kind === 'progress' || kind === 'heartbeat') {
      // Only the newest status matters.
      for (let index = outbox.length - 1; index >= 0; index -= 1) {
        if (outbox[index].kind === kind && outbox[index].payload.frame === payload.frame) outbox.splice(index, 1);
      }
    }
    if (kind === 'debug' && outbox.length >= OUTBOX_MAX) return;
    outbox.push({ kind, payload });
    pump();
  };

  const signal = (kind, extra = {}) => {
    const payload = { mode, frame, host, ...extra };
    try {
      if (isTop) {
        enqueue(kind, payload);
      } else {
        window.top.postMessage({ __SSU_WATCH_SIGNAL__: true, kind, payload }, '*');
      }
    } catch (_) {}
  };

  if (isTop) {
    try {
      window.addEventListener('message', (event) => {
        const data = event && event.data;
        if (!data || data.__SSU_WATCH_SIGNAL__ !== true || !data.payload) return;
        enqueue(String(data.kind), { ...data.payload, frame: data.payload.frame || 'child' });
      });
    } catch (_) {}
  }

  let lastDebug = '';
  const debug = (stage, extra = {}) => {
    const payload = {
      stage,
      ready: document.readyState,
      videos: String(document.querySelectorAll('video').length),
      buttons: String(document.querySelectorAll('button,[role="button"],.vjs-big-play-button,.vjs-play-control').length),
      ...extra
    };
    const key = JSON.stringify(payload);
    if (key === lastDebug) return;
    lastDebug = key;
    signal('debug', payload);
  };

  // ------------------------------------------------------------- page rendering
  // Animation frames only run while the page is really being drawn, so a recent frame is a
  // read-only sign that the window is on screen. Time without frames never counts as a stall.

  let lastFrameAt = performance.now();
  const onFrame = () => {
    lastFrameAt = performance.now();
    window.requestAnimationFrame(onFrame);
  };
  try { window.requestAnimationFrame(onFrame); } catch (_) {}
  const isRendering = () => performance.now() - lastFrameAt < FRAME_ALIVE_MS;

  // ----------------------------------------------------------------- video state

  const videoDuration = (video) => Number.isFinite(video && video.duration) ? video.duration : 0;
  const isLikelyMainVideo = (video) => videoDuration(video) > SHORT_INTRO_MAX_SECONDS;

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
    const usable = videos.filter((video) => {
      const rect = video.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && (video.currentSrc || video.src || video.readyState > 0);
    });
    return usable.find(isLikelyMainVideo) || usable[0] || videos.find(isLikelyMainVideo) || videos[0];
  };

  let endedSent = false;
  const notifyEnded = (video) => {
    if (!isLikelyMainVideo(video)) {
      debug('short-video-ended', { video: videoState(video) });
      return;
    }
    if (!shouldEmitEnded || endedSent) {
      debug('main-video-ended', { video: videoState(video) });
      return;
    }
    endedSent = true;
    debug('ended', { video: videoState(video) });
    signal('ended', { time: round1(video.currentTime), duration: round1(video.duration) });
  };

  const round1 = (value) => Number.isFinite(value) ? String(Math.round(value * 10) / 10) : '';
  // Progress always carries both numbers; a length that is not known yet counts as 0.
  const seconds = (value) => Number.isFinite(value) ? String(Math.round(value * 10) / 10) : '0';

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

  // Muted playback is what keeps an unattended window from blaring; muting is always allowed.
  // It is done once per video, so a person who unmutes by hand is not fought.
  const muteOnce = (video) => {
    if (video.__SSU_WATCH_MUTED__ === video.__SSU_WATCH_ASSIST_ATTACHED__) return;
    video.__SSU_WATCH_MUTED__ = video.__SSU_WATCH_ASSIST_ATTACHED__;
    try { video.muted = true; } catch (_) {}
    debug('muted', { video: videoState(video) });
  };

  let playDelay = PLAY_RETRY_MIN_MS;
  let nextPlayAt = 0;
  let playAttempts = 0;
  const tryPlayVideo = (video, now) => {
    if (now < nextPlayAt) return;
    playAttempts += 1;
    nextPlayAt = now + playDelay;
    playDelay = Math.min(playDelay * 2, PLAY_RETRY_MAX_MS);
    try {
      debug('play-attempt', { video: videoState(video), attempt: String(playAttempts) });
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
  };

  const clickLikelyPlayControl = () => {
    const selectors = [
      '.vjs-big-play-button',
      '.vjs-play-control',
      '.play-button',
      '.btn-play',
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

  // --------------------------------------------------------------- resume dialog
  // After play the Commons player asks "이전에 시청했던 07:29부터 이어서 보시겠습니까?" with
  // 예 / 아니오. Answering 아니오 leaves the player on a loading spinner, so always answer 예.

  const normalizeText = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const controlLabel = (element) => normalizeText(element.innerText || element.textContent || element.value);
  const isShown = (element) => {
    try {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    } catch (_) {
      return false;
    }
  };

  // A control whose whole label is "예" and that sits in the same dialog as the question.
  const pickYesInDialog = (candidates) => {
    for (const control of candidates) {
      if (controlLabel(control) !== RESUME_YES_LABEL || !isShown(control)) continue;
      let scope = control.parentElement;
      for (let depth = 0; scope && depth < 6; depth += 1, scope = scope.parentElement) {
        if (normalizeText(scope.textContent).includes(RESUME_DIALOG_TEXT)) return control;
      }
    }
    return null;
  };

  const findResumeYesButton = () => {
    const body = document.body;
    if (!body || !normalizeText(body.textContent).includes(RESUME_DIALOG_TEXT)) return null;
    // Real buttons first; players that draw their buttons as plain elements come second.
    const buttons = Array.from(document.querySelectorAll('button,[role="button"],a,input[type="button"],input[type="submit"]'));
    return pickYesInDialog(buttons)
      || pickYesInDialog(Array.from(document.querySelectorAll('div,span,li,p')).filter((element) => !element.children || element.children.length === 0));
  };

  let resumeClicks = 0;
  let lastResumeClickAt = 0;
  const answerResumeDialog = (now) => {
    if (resumeClicks >= RESUME_MAX_CLICKS || now - lastResumeClickAt < RESUME_CLICK_COOLDOWN_MS) return false;
    const button = findResumeYesButton();
    if (!button) return false;
    resumeClicks += 1;
    lastResumeClickAt = now;
    // The player loads again after the answer; that waiting is not a stall.
    lastAdvanceAt = now;
    try {
      button.click();
      debug('resume-dialog-yes', { attempt: String(resumeClicks) });
    } catch (error) {
      debug('resume-dialog-error', {
        error: error && error.name ? error.name : 'unknown',
        message: error && error.message ? error.message : 'no-message'
      });
    }
    return true;
  };

  // --------------------------------------------------------- progress and stalls

  let lastSampleTime = -1;
  let lastAdvanceAt = Date.now();
  let stallReported = false;
  let lastProgressAt = 0;
  let playingSince = 0;
  let videoSeenAt = 0;
  let lastPlayClickAt = 0;

  const trackAdvance = (video, now) => {
    const current = video.currentTime;
    if (!isRendering()) {
      // A covered or minimized window stops the player; that is not a stall.
      lastAdvanceAt = now;
      return;
    }
    if (Number.isFinite(current) && Math.abs(current - lastSampleTime) >= 0.25) {
      lastSampleTime = current;
      lastAdvanceAt = now;
      stallReported = false;
      return;
    }
    if (!stallReported && !video.ended && now - lastAdvanceAt >= STALL_AFTER_MS) {
      stallReported = true;
      debug('stalled', { video: videoState(video) });
      signal('stalled', { time: round1(current), duration: round1(video.duration) });
    }
  };

  const reportProgress = (video, now, force) => {
    if (!force && now - lastProgressAt < PROGRESS_INTERVAL_MS) return;
    lastProgressAt = now;
    signal('progress', {
      time: seconds(video.currentTime),
      duration: seconds(video.duration),
      main: isLikelyMainVideo(video) ? '1' : '0',
      paused: video.paused ? '1' : '0',
      rendering: isRendering() ? '1' : '0'
    });
  };

  if (isTop) {
    setInterval(() => {
      signal('heartbeat', { rendering: isRendering() ? '1' : '0' });
    }, HEARTBEAT_INTERVAL_MS);
  }

  // ------------------------------------------------------------------- main loop

  debug('installed');
  let ticks = 0;
  const tick = () => {
    ticks += 1;
    const now = Date.now();
    try {
      const video = findVideo();
      if (video && !videoSeenAt) {
        videoSeenAt = now;
        lastAdvanceAt = now;
      }
      // Answer the dialog until playback has run on for a while.
      if (!playingSince || now - playingSince < 15000) answerResumeDialog(now);
      if (video && !endedSent) {
        attachVideo(video);
        muteOnce(video);
        if (video.paused && !video.ended) {
          playingSince = 0;
          tryPlayVideo(video, now);
          if (playAttempts >= 2 && now - lastPlayClickAt >= 5000 && !findResumeYesButton()) {
            lastPlayClickAt = now;
            clickLikelyPlayControl();
          }
        } else {
          playDelay = PLAY_RETRY_MIN_MS;
          nextPlayAt = 0;
          if (!playingSince) playingSince = now;
        }
        trackAdvance(video, now);
        reportProgress(video, now, false);
      } else if (!video && (ticks === 1 || ticks % 20 === 0)) {
        debug('no-video', { attempt: String(ticks) });
        if (isTop && !findResumeYesButton()) clickLikelyPlayControl();
      }
    } catch (error) {
      debug('tick-error', {
        error: error && error.name ? error.name : 'unknown',
        message: error && error.message ? error.message : 'no-message'
      });
    }
    setTimeout(tick, ticks < FAST_TICKS ? FAST_TICK_MS : SLOW_TICK_MS);
  };
  tick();
})();
