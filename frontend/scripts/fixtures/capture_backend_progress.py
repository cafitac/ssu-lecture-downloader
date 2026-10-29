"""Capture real compacted progress lists from the pl-py-pipeline test rig (backend 3b1a6e7).

Usage: uv run python capture_backend_progress.py <out dir> <pl-py-pipeline worktree>   (run with cwd = that worktree)
Writes remote.json and cap.json; the fixtures next to this file are those two files wrapped with a source header.
"""
import json, sys, tempfile, threading
from pathlib import Path
WT = Path(sys.argv[2])
sys.path[:0] = [str(WT / 'tests'), str(WT / 'src')]
import pytest
import test_artifact_pipeline as t
from ssu_lecture_downloader.core import postprocess_media, stt
from stt_fakes import FakeSttServer, srt, write_silent_wav

OUT = Path(sys.argv[1])

def scenario_remote():
    mp = pytest.MonkeyPatch()
    tmp = Path(tempfile.mkdtemp())
    specs = [t.Spec(n, has_video=True) for n in range(1, 5)]
    rig = t.Rig(tmp, mp, specs)
    for s in specs:
        rig.lms.video_path(s.item_id).with_suffix('.mp3').write_bytes(b'audio')
    mp.setattr(stt, 'transcribe_audio', t.REAL_TRANSCRIBE_AUDIO)
    def convert(src, dst, should_cancel=None):
        t.simulate_work(0.15); write_silent_wav(dst, 60)
    def cut(src, dst, start, end, should_cancel=None):
        t.simulate_work(0.1); write_silent_wav(dst, max(1, end - start))
    mp.setattr(postprocess_media, 'convert_to_stt_wav', convert)
    mp.setattr(postprocess_media, 'cut_audio_segment', cut)
    first = srt((0, 4, '안녕하세요'), (4, 6, '음'), *[(20 + i, 21 + i, '같은 문장') for i in range(10)], (59.9, 60, '마무리'))
    repair = srt((2, 4, '복구된 문장'), (30, 32, '창 밖 문장'))
    def respond(request):
        t.simulate_work(0.3)
        return 200, 'text/plain', repair if request.fields['temperature'] == '0.2' else first
    with FakeSttServer(respond) as server:
        rig.enable_remote(server.url)
        snap = rig.run(['txt'], stt_provider='faster_whisper')
    assert snap['status'] == 'succeeded', snap['error_code']
    return snap

def scenario_cap():
    mp = pytest.MonkeyPatch()
    tmp = Path(tempfile.mkdtemp())
    rig = t.Rig(tmp, mp, [t.Spec(n, has_video=True, block_extract=True) for n in range(1, 31)])
    inner = rig._extract
    noisy = {'finished': 0}; lock = threading.Lock()
    def chatty(request, on_progress=None, should_cancel=None):
        for tick in range(500):
            on_progress({'stage': 'audio_note', 'tick': tick})
        with lock: noisy['finished'] += 1
        rig.signal.notify()
        return inner(request, on_progress=on_progress, should_cancel=should_cancel)
    mp.setattr(postprocess_media, 'extract_audio', chatty)
    job = rig.start(['mp3'])
    rig.wait_for(lambda: noisy['finished'] == 2 and len(t.events(job, 'audio_waiting')) == 28, message='noisy done')
    prog = job.progress_events()
    t.cancel_and_wait(rig, job)
    return prog

snap = scenario_remote()
json.dump(snap['progress'], open(OUT / 'remote.json', 'w'), ensure_ascii=False)
cap = scenario_cap()
json.dump(cap, open(OUT / 'cap.json', 'w'), ensure_ascii=False)
print(len(snap['progress']), len(cap))
