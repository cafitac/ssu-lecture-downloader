"""Execute the complete observe-only helper against deterministic light/shadow DOM stubs."""

import json
import os
import shutil
import subprocess

import pytest

from ssu_lecture_downloader.core.chrome_watch_helper import HELPER_JS


NODE_HARNESS = r"""
const vm = require('node:vm');
const source = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const element = (name = 'div') => ({localName: name, firstElementChild: null,
  nextElementSibling: null, shadowRoot: null});
const children = (parent, nodes) => {
  parent.firstElementChild = nodes[0] || null;
  nodes.forEach((node, i) => { node.nextElementSibling = nodes[i + 1] || null; });
};
const video = (duration, paused = false, ended = false) => Object.assign(element('video'), {
  duration, paused, ended, readyState: 3, currentTime: 1.24, src: 'fixture',
  getBoundingClientRect: () => ({width: 100, height: 100}), addEventListener: () => {},
});
const audio = (duration, paused = false, ended = false) => Object.assign(video(duration, paused, ended), {
  localName: 'audio', getBoundingClientRect: () => ({width: 0, height: 0}),
});
const baseVideoPicks = {};
const run = (build, after = () => {}, parityKey = null) => {
  const root = element('html');
  const picked = video(20, true);
  const light = [picked];
  children(root, light);
  build(root, light, picked);
  const signals = [];
  const window = {location: {hostname: 'fixture.example'}, ssuWatchSignal: (value) => signals.push(JSON.parse(value))};
  window.top = window;
  const document = {documentElement: root, body: {textContent: ''},
    querySelector: () => null,
    querySelectorAll: (selector) => selector === 'video,audio' ? light : selector === 'video' ? light.filter((el) => el.localName === 'video') : []};
  vm.runInNewContext(source, {window, document, setInterval: () => {}}, {timeout: 2000});
  after(window, signals);
  const state = window.__ssuWatchState();
  if (parityKey) {
    // Base f2e3f66 findVideo, with its original selection order.
    const isMain = (video) => Number.isFinite(video.duration) && video.duration > 10;
    const findVideo = () => {
      const videos = Array.from(document.querySelectorAll('video'));
      const usable = videos.filter((video) => {
        const rect = video.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && (video.currentSrc || video.src || video.readyState > 0);
      });
      return usable.find(isMain) || usable[0] || videos.find(isMain) || videos[0] || null;
    };
    const expected = findVideo();
    baseVideoPicks[parityKey] = {
      tag: 'video', time: expected.currentTime, duration: expected.duration,
      paused: expected.paused, ended: expected.ended, main: isMain(expected), ready: expected.readyState,
    };
  }
  return state;
};
const results = {};
results.nested = run((root, light, picked) => {
  const host = element(); const inner = element();
  host.shadowRoot = element(); inner.shadowRoot = element();
  children(inner.shadowRoot, [video(120), video(5), video(40, false, true)]);
  children(host.shadowRoot, [inner]); children(root, [picked, host]);
}, () => {}, 'nested');
results.lightPastCap = run((root, light, picked) => {
  const late = video(120); light.push(late);
  children(root, [picked, ...Array.from({length: 2000}, () => element()), late]);
});
results.shadowPastCap = run((root, light, picked) => {
  const host = element(); host.shadowRoot = element();
  children(host.shadowRoot, [video(120)]);
  children(root, [picked, ...Array.from({length: 2000}, () => element()), host]);
});
results.depth = run((root, light, picked) => {
  let host = element(); children(root, [picked, host]);
  for (let depth = 1; depth <= 5; depth++) {
    host.shadowRoot = element();
    const next = element();
    children(host.shadowRoot, [video(120), next]);
    host = next;
  }
});
results.plain = run(() => {}, () => {}, 'plain');
results.twoVisibleMainVideos = run((root, light) => {
  light.push(Object.assign(video(3600), {currentTime: 777})); children(root, light);
}, () => {}, 'twoVisibleMainVideos');
results.failure = run((root) => {
  Object.defineProperty(root, 'shadowRoot', {get: () => { throw new Error('patched getter'); }});
});
results.liveAudio = run((root, light, picked) => {
  Object.assign(picked, {duration: NaN, readyState: 0});
  light.push(Object.assign(audio(3910), {readyState: 4, currentTime: 459.6}));
  children(root, light);
});
let pausedAudioOverUnreadyVideo;
results.pausedAudioOverUnreadyVideo = run((root, light, picked) => {
  Object.assign(picked, {duration: NaN, readyState: 0});
  pausedAudioOverUnreadyVideo = Object.assign(audio(3910), {readyState: 4, currentTime: 459.6});
  light.push(pausedAudioOverUnreadyVideo); children(root, light);
}, (window) => {
  results.beforePauseOverUnreadyVideo = window.__ssuWatchState();
  pausedAudioOverUnreadyVideo.paused = true;
});
let endedAudio; let endedListener;
results.endedAudio = run((root, light, picked) => {
  Object.assign(picked, {duration: NaN, readyState: 0});
  endedAudio = audio(3910);
  endedAudio.addEventListener = (name, listener) => { if (name === 'ended') endedListener = listener; };
  light.push(endedAudio); children(root, light);
}, (window, signals) => {
  endedAudio.ended = true; endedAudio.paused = true;
  endedListener(); endedListener(); window.__ssuWatchState();
  results.endedSignals = signals.filter((signal) => signal.kind === 'ended');
});
results.shortAudioOnly = run((root, light) => {
  light.splice(0, light.length, audio(5)); children(root, light);
});
let pausedAudio;
results.pausedAudio = run((root, light) => {
  pausedAudio = audio(3910);
  light.push(pausedAudio); children(root, light);
}, (window) => {
  results.beforePause = window.__ssuWatchState();
  pausedAudio.paused = true;
});
let removedAudio; let removedRoot; let removedLight;
results.removedAudio = run((root, light, picked) => {
  picked.duration = 5;
  removedRoot = root; removedLight = light;
  removedAudio = Object.assign(audio(3910), {isConnected: true});
  light.push(removedAudio); children(root, light);
}, (window) => {
  results.beforeRemoval = window.__ssuWatchState();
  removedLight[0].duration = 20;
  removedAudio.isConnected = false; removedAudio.ended = true; removedAudio.paused = true;
  removedLight.splice(removedLight.indexOf(removedAudio), 1); children(removedRoot, removedLight);
});
results.onlyAudio = run((root, light) => {
  light.splice(0, light.length, audio(5, true), audio(3910, true)); children(root, light);
});
let secondAudio;
results.twoAudios = run((root, light) => {
  secondAudio = Object.assign(audio(3600), {currentTime: 777});
  light.splice(0, light.length, audio(3910, true), secondAudio); children(root, light);
}, (window) => {
  results.beforeSecondAudioPause = window.__ssuWatchState();
  secondAudio.paused = true;
});
results.baseVideoParity = baseVideoPicks;
process.stdout.write(JSON.stringify(results));
"""


@pytest.fixture(scope='module')
def snapshots():
    node = shutil.which('node')
    if node is None:
        if 'CI' in os.environ:
            pytest.fail('node is required for helper counts in CI')
        pytest.skip('node is unavailable locally')
    result = subprocess.run(
        [node, '-e', NODE_HARNESS], input=json.dumps(HELPER_JS), text=True,
        capture_output=True, timeout=15, check=True,
        env={key: value for key, value in os.environ.items() if key not in {'CI', 'NODE_OPTIONS'}},
    )
    return json.loads(result.stdout)


def test_helper_counts_nested_shadow_videos_without_changing_pick(snapshots):
    state = snapshots['nested']
    assert (state['videoCount'], state['playingCount'], state['playingLong'], state['scanCapped']) == (4, 2, 1, False)
    # The paused first light-DOM video remains selected despite the playing shadow video.
    assert state['video'] == {'time': 1.24, 'duration': 20, 'paused': True, 'ended': False, 'main': True, 'ready': 3, 'tag': 'video'}
    assert state['front'] is False and state['resume'] is False


def test_helper_light_count_is_unbounded_but_shadow_walk_is_capped(snapshots):
    light = snapshots['lightPastCap']
    shadow = snapshots['shadowPastCap']
    assert (light['videoCount'], light['playingCount'], light['playingLong'], light['scanCapped']) == (2, 1, 1, True)
    assert (shadow['videoCount'], shadow['playingCount'], shadow['playingLong'], shadow['scanCapped']) == (1, 0, 0, True)


def test_helper_shadow_depth_four_is_counted_but_five_is_capped(snapshots):
    state = snapshots['depth']
    assert (state['videoCount'], state['playingCount'], state['playingLong'], state['scanCapped']) == (5, 4, 4, True)


def test_helper_count_failure_preserves_all_other_snapshot_fields(snapshots):
    failed = snapshots['failure'].copy()
    plain = snapshots['plain'].copy()
    for key in ('videoCount', 'playingCount', 'playingLong', 'scanCapped', 'audioCount', 'playingAudio'):
        assert failed.pop(key) is None
        plain.pop(key)
    assert failed == plain == {
        'kind': 'state', 'top': True, 'host': 'fixture.example', 'front': False, 'resume': False,
        'video': {'time': 1.24, 'duration': 20, 'paused': True, 'ended': False, 'main': True, 'ready': 3, 'tag': 'video'},
    }


def test_live_audio_wins_over_unready_visible_video(snapshots):
    state = snapshots['liveAudio']
    assert state['video'] == {'tag': 'audio', 'time': 459.6, 'duration': 3910, 'paused': False,
                              'ended': False, 'main': True, 'ready': 4}
    assert (state['audioCount'], state['playingAudio'], state['videoCount']) == (1, 1, 1)


def test_ended_audio_signal_latch(snapshots):
    assert snapshots['endedAudio']['video']['tag'] == 'audio'
    assert snapshots['endedAudio']['video']['ended'] is True
    assert snapshots['endedAudio']['playingAudio'] == 0
    assert len(snapshots['endedSignals']) == 1
    assert snapshots['endedSignals'][0]['duration'] == 3910


def test_paused_audio_only_page_selects_main_audio(snapshots):
    assert snapshots['onlyAudio']['video']['tag'] == 'audio'
    assert snapshots['onlyAudio']['video']['duration'] == 3910
    assert snapshots['onlyAudio']['video']['paused'] is True


def test_main_video_wins_over_playing_audio(snapshots):
    for key in ('beforePause', 'pausedAudio'):
        assert snapshots[key]['video'] == snapshots['plain']['video']
        assert snapshots[key]['video']['tag'] == 'video'
        assert snapshots[key]['video']['duration'] == 20


def test_removed_ended_live_audio_returns_to_legacy_video(snapshots):
    assert snapshots['beforeRemoval']['video']['tag'] == 'audio'
    assert snapshots['beforeRemoval']['video']['ended'] is False
    assert snapshots['removedAudio']['video'] == snapshots['plain']['video']
    assert snapshots['removedAudio']['video']['tag'] == 'video'
    assert snapshots['removedAudio']['video']['time'] == 1.24


def test_paused_live_audio_stays_selected_over_unready_video(snapshots):
    before = snapshots['beforePauseOverUnreadyVideo']['video']
    assert before['tag'] == 'audio' and before['paused'] is False
    assert snapshots['pausedAudioOverUnreadyVideo']['video'] == {
        'tag': 'audio', 'time': 459.6, 'duration': 3910, 'paused': True,
        'ended': False, 'main': True, 'ready': 4,
    }


def test_short_audio_only_is_not_reported_as_progress(snapshots):
    assert snapshots['shortAudioOnly']['video'] is None



def test_playing_second_audio_stays_selected_after_pausing(snapshots):
    before = snapshots['beforeSecondAudioPause']['video']
    after = snapshots['twoAudios']['video']
    assert before['tag'] == after['tag'] == 'audio'
    assert before['duration'] == after['duration'] == 3600
    assert before['time'] == after['time'] == 777
    assert before['paused'] is False
    assert after['paused'] is True


@pytest.mark.parametrize('fixture', ['plain', 'nested', 'twoVisibleMainVideos'])
def test_pure_video_selection_matches_base(snapshots, fixture):
    assert snapshots[fixture]['video'] == snapshots['baseVideoParity'][fixture]
    assert snapshots[fixture]['video']['duration'] == 20
    assert snapshots[fixture]['video']['paused'] is True
