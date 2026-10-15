import { useCallback, useEffect, useState } from 'react';
import { fetchMediaDependencyInstallJob, fetchMediaDependencyStatus, fetchSttProviderStatus, startMediaDependencyInstallJob } from '../lib/api';
import { describeErrorCode, friendlyError } from '../lib/errors';
import { TERMINAL_DOWNLOAD_STATUSES } from '../lib/progress';
import type { DownloadOutputFormat, MediaDependencyInstallResult, PostprocessJobStatus, RuntimeDependencyStatus, STTProviderStatus } from '../lib/types';
import { usePolledJob } from './usePolledJob';

export type MediaComponent = 'ffmpeg' | 'faster_whisper';

interface Options {
  onMessage: (message: string, kind?: 'info' | 'success' | 'error') => void;
  /** True while a download is starting/running; blocks auto-install. */
  downloadBusy: boolean;
}

/** Components an output needs: MP3 needs ffmpeg, TXT needs ffmpeg and the STT engine. */
export function componentsFor(formats: DownloadOutputFormat[]): MediaComponent[] {
  const needed = new Set<MediaComponent>();
  if (formats.includes('mp3') || formats.includes('txt')) needed.add('ffmpeg');
  if (formats.includes('txt')) needed.add('faster_whisper');
  return Array.from(needed);
}

/** ffmpeg / STT readiness for MP3 and TXT outputs, plus the one-click auto install job. */
export function useMediaDependencies({ onMessage, downloadBusy }: Options) {
  const [sttProviders, setSttProviders] = useState<STTProviderStatus[]>([]);
  const [runtimeDependencies, setRuntimeDependencies] = useState<RuntimeDependencyStatus[]>([]);
  const [autoInstallAvailable, setAutoInstallAvailable] = useState(false);
  const [autoInstallUnavailableReason, setAutoInstallUnavailableReason] = useState<string | null>(null);
  const [installJob, setInstallJob] = useState<PostprocessJobStatus<MediaDependencyInstallResult> | null>(null);

  const load = useCallback(async () => {
    try {
      const status = await fetchMediaDependencyStatus();
      setRuntimeDependencies(status.dependencies);
      setSttProviders(status.providers);
      setAutoInstallAvailable(Boolean(status.auto_install_available));
      setAutoInstallUnavailableReason(status.auto_install_unavailable_reason ?? null);
    } catch {
      setRuntimeDependencies([]);
      setAutoInstallAvailable(false);
      setAutoInstallUnavailableReason('구성요소 상태를 확인하지 못했어요. 앱을 다시 시작해 주세요.');
      await fetchSttProviderStatus().then((response) => setSttProviders(response.providers)).catch(() => setSttProviders([]));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const applyInstallOutcome = useCallback(async (job: PostprocessJobStatus<MediaDependencyInstallResult>) => {
    if (job.status === 'succeeded') {
      onMessage('음성·자막 구성요소를 설치했어요.', 'success');
      await load();
    } else if (job.status === 'failed') {
      onMessage(describeErrorCode(job.result?.error_code ?? job.error_code, '구성요소를 설치하지 못했어요. 다시 시도해 주세요.'), 'error');
    } else if (job.status === 'cancelled') {
      onMessage('구성요소 설치를 취소했어요.', 'info');
    }
  }, [load, onMessage]);

  usePolledJob(installJob, {
    fetch: fetchMediaDependencyInstallJob,
    isTerminal: (job) => TERMINAL_DOWNLOAD_STATUSES.has(job.status),
    onUpdate: async (job) => {
      setInstallJob((current) => (current && current.job_id === job.job_id ? job : current));
      await applyInstallOutcome(job);
    },
    onError: (error) => onMessage(friendlyError(error, '설치 진행 상황을 확인하지 못했어요.'), 'error'),
  });

  const whisperStatus = sttProviders.find((provider) => provider.provider === 'faster_whisper');
  const ffmpegDependency = runtimeDependencies.find((dependency) => dependency.name === 'ffmpeg');
  const missingComponents: MediaComponent[] = [
    ffmpegDependency && !ffmpegDependency.available ? 'ffmpeg' as const : null,
    whisperStatus && !whisperStatus.available ? 'faster_whisper' as const : null,
  ].filter((component): component is MediaComponent => Boolean(component));
  const isInstalling = installJob?.status === 'queued' || installJob?.status === 'running';

  /** Components still missing for the given outputs (empty for MP4 only). */
  const missingFor = useCallback(
    (formats: DownloadOutputFormat[]) => componentsFor(formats).filter((component) => missingComponents.includes(component)),
    [missingComponents],
  );

  const install = useCallback(async (components: MediaComponent[] = missingComponents) => {
    if (components.length === 0 || !autoInstallAvailable || isInstalling || downloadBusy) return;
    try {
      const job = await startMediaDependencyInstallJob(components);
      setInstallJob(job);
      await applyInstallOutcome(job);
    } catch (error) {
      onMessage(friendlyError(error, '구성요소 설치를 시작하지 못했어요.'), 'error');
    }
  }, [applyInstallOutcome, autoInstallAvailable, downloadBusy, isInstalling, missingComponents, onMessage]);

  return {
    load,
    install,
    isInstalling,
    canInstall: autoInstallAvailable && !isInstalling && !downloadBusy,
    missingComponents,
    missingFor,
    ffmpegDependency,
    whisperStatus,
    autoInstallAvailable,
    autoInstallUnavailableReason,
  };
}

export type MediaDependencies = ReturnType<typeof useMediaDependencies>;
