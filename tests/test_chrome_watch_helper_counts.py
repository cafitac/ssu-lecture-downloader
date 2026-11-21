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
const run = (build) => {
  const root = element('html');
  const picked = video(20, true);
  const light = [picked];
  children(root, light);
  build(root, light, picked);
  const window = {location: {hostname: 'fixture.example'}};
  window.top = window;
  const document = {documentElement: root, body: {textContent: ''},
    querySelector: () => null,
    querySelectorAll: (selector) => selector === 'video' ? light : []};
  vm.runInNewContext(source, {window, document, setInterval: () => {}}, {timeout: 2000});
  return window.__ssuWatchState();
};
const results = {};
results.nested = run((root, light, picked) => {
  const host = element(); const inner = element();
  host.shadowRoot = element(); inner.shadowRoot = element();
  children(inner.shadowRoot, [video(120), video(5), video(40, false, true)]);
  children(host.shadowRoot, [inner]); children(root, [picked, host]);
});
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
results.plain = run(() => {});
results.failure = run((root) => {
  Object.defineProperty(root, 'shadowRoot', {get: () => { throw new Error('patched getter'); }});
});
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
    assert state['video'] == {'time': 1.24, 'duration': 20, 'paused': True, 'ended': False, 'main': True, 'ready': 3}
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
    for key in ('videoCount', 'playingCount', 'playingLong', 'scanCapped'):
        assert failed.pop(key) is None
        plain.pop(key)
    assert failed == plain == {
        'kind': 'state', 'top': True, 'host': 'fixture.example', 'front': False, 'resume': False,
        'video': {'time': 1.24, 'duration': 20, 'paused': True, 'ended': False, 'main': True, 'ready': 3},
    }
