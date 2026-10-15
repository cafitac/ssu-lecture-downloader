import { useCallback, useState } from 'react';
import { fetchAudioExtractionJob, fetchSttJob, startAudioExtractionJob, startSttJob } from '../lib/api';
import { openDownloadedFolder } from '../lib/desktop';
import { TERMINAL_DOWNLOAD_STATUSES } from '../lib/progress';
import type { AudioExtractionResult, PostprocessJobStatus, TranscriptionResult } from '../lib/types';
import { usePolledJob } from './usePolledJob';

interface Options {
  /** Video path to use when the user has not typed one (e.g. the last downloaded file). */
  defaultVideoPath: string;
  onMessage: (message: string) => void;
  /** Routes an error to the shared handler (auth failures return to the login screen). */
  onError: (error: unknown, fallback: string) => void | Promise<void>;
}

/** Manual audio-extraction and STT jobs for an already downloaded video. */
export function usePostprocessJobs({ defaultVideoPath, onMessage, onError }: Options) {
  const [inputPath, setInputPath] = useState('');
  const [audioJob, setAudioJob] = useState<PostprocessJobStatus<AudioExtractionResult> | null>(null);
  const [sttJob, setSttJob] = useState<PostprocessJobStatus<TranscriptionResult> | null>(null);

  usePolledJob(audioJob, {
    fetch: fetchAudioExtractionJob,
    isTerminal: (job) => TERMINAL_DOWNLOAD_STATUSES.has(job.status),
    onUpdate: (job) => {
      setAudioJob((current) => (current && current.job_id === job.job_id ? job : current));
      if (TERMINAL_DOWNLOAD_STATUSES.has(job.status)) onMessage(job.status === 'succeeded' ? '음성 파일 추출이 끝났습니다.' : '음성 추출 작업이 종료되었습니다.');
    },
    onError: (error) => onError(error, '음성 추출 상태 조회 실패'),
  });

  usePolledJob(sttJob, {
    fetch: fetchSttJob,
    isTerminal: (job) => TERMINAL_DOWNLOAD_STATUSES.has(job.status),
    onUpdate: (job) => {
      setSttJob((current) => (current && current.job_id === job.job_id ? job : current));
      if (TERMINAL_DOWNLOAD_STATUSES.has(job.status)) onMessage(job.status === 'succeeded' ? 'STT 파일 생성이 끝났습니다.' : 'STT 작업이 종료되었습니다.');
    },
    onError: (error) => onError(error, 'STT 상태 조회 실패'),
  });

  const videoPath = inputPath || defaultVideoPath;
  const extractedAudioPath = audioJob?.result?.output_path ?? '';
  const transcriptTextPath = sttJob?.result?.text_path ?? '';
  const isActive = Boolean(
    (audioJob && !TERMINAL_DOWNLOAD_STATUSES.has(audioJob.status))
    || (sttJob && !TERMINAL_DOWNLOAD_STATUSES.has(sttJob.status)),
  );

  const clear = useCallback(() => {
    setAudioJob(null);
    setSttJob(null);
    setInputPath('');
  }, []);

  const extractAudio = useCallback(async () => {
    const path = videoPath.trim();
    if (!path) {
      onMessage('음성을 추출할 영상 파일 경로가 필요합니다.');
      return;
    }
    onMessage('영상 파일에서 음성 파일을 추출하는 중입니다.');
    try {
      const job = await startAudioExtractionJob(path, 'wav', false);
      setAudioJob(job);
      if (job.status === 'failed' && job.result?.install_hint) onMessage(job.result.install_hint);
    } catch (error) {
      await onError(error, '음성 추출을 시작하지 못했습니다.');
    }
  }, [onError, onMessage, videoPath]);

  const runStt = useCallback(async () => {
    const audioPath = extractedAudioPath.trim();
    if (!audioPath) {
      onMessage('먼저 음성 추출을 완료해 주세요.');
      return;
    }
    onMessage('정밀 STT를 실행하는 중입니다. 처음 실행이면 Whisper large-v3 모델 준비가 오래 걸릴 수 있습니다.');
    try {
      const job = await startSttJob(audioPath, 'faster_whisper', 'large-v3', false);
      setSttJob(job);
      if (job.status === 'failed' && job.result?.install_hint) onMessage(job.result.install_hint);
    } catch (error) {
      await onError(error, 'STT를 시작하지 못했습니다.');
    }
  }, [extractedAudioPath, onError, onMessage]);

  const openTranscriptFolder = useCallback(async () => {
    const path = transcriptTextPath || extractedAudioPath || videoPath;
    if (!path) return;
    try {
      await openDownloadedFolder(path);
      onMessage('파일 위치를 Finder에서 열었습니다.');
    } catch (error) {
      await onError(error, '파일 위치를 열 수 없습니다.');
    }
  }, [extractedAudioPath, onError, onMessage, transcriptTextPath, videoPath]);

  return { inputPath, setInputPath, videoPath, audioJob, sttJob, isActive, clear, extractAudio, runStt, openTranscriptFolder };
}
