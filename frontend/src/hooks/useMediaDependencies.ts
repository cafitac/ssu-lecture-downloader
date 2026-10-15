import { useCallback, useEffect, useState } from 'react';
import { fetchMediaDependencyInstallJob, fetchMediaDependencyStatus, fetchSttProviderStatus, startMediaDependencyInstallJob } from '../lib/api';
import { TERMINAL_DOWNLOAD_STATUSES } from '../lib/progress';
import type { MediaDependencyInstallResult, PostprocessJobStatus, RuntimeDependencyStatus, STTProviderStatus } from '../lib/types';
import { usePolledJob } from './usePolledJob';

interface Options {
  onMessage: (message: string) => void;
  /** True while a download is starting/running; blocks auto-install. */
  downloadBusy: boolean;
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
      setAutoInstallUnavailableReason('구성요소 상태 API에 연결할 수 없습니다. 앱/API 서버를 재시작해 주세요.');
      await fetchSttProviderStatus().then((response) => setSttProviders(response.providers)).catch(() => setSttProviders([]));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const applyInstallOutcome = useCallback(async (job: PostprocessJobStatus<MediaDependencyInstallResult>) => {
    if (job.status === 'succeeded') {
      onMessage(job.result?.message ?? '구성요소 설치가 끝났습니다.');
      await load();
    } else if (job.status === 'failed') {
      onMessage(job.result?.message ?? '구성요소 자동 설치에 실패했습니다.');
    }
  }, [load, onMessage]);

  usePolledJob(installJob, {
    fetch: fetchMediaDependencyInstallJob,
    isTerminal: (job) => TERMINAL_DOWNLOAD_STATUSES.has(job.status),
    onUpdate: async (job) => {
      setInstallJob((current) => (current && current.job_id === job.job_id ? job : current));
      await applyInstallOutcome(job);
    },
    onError: (error) => onMessage(error instanceof Error ? error.message : '구성요소 설치 상태 조회 실패'),
  });

  const whisperStatus = sttProviders.find((provider) => provider.provider === 'faster_whisper');
  const ffmpegDependency = runtimeDependencies.find((dependency) => dependency.name === 'ffmpeg');
  const missingComponents = [
    ffmpegDependency && !ffmpegDependency.available ? 'ffmpeg' : null,
    whisperStatus && !whisperStatus.available ? 'faster_whisper' : null,
  ].filter((component): component is string => Boolean(component));
  const isInstalling = installJob?.status === 'queued' || installJob?.status === 'running';
  const canInstall = missingComponents.length > 0 && autoInstallAvailable && !isInstalling && !downloadBusy;
  const ready = missingComponents.length === 0;

  const install = useCallback(async () => {
    if (!canInstall) return;
    onMessage('필요한 MP3/TXT 구성요소만 자동 설치를 시작합니다.');
    try {
      const job = await startMediaDependencyInstallJob(missingComponents);
      setInstallJob(job);
      await applyInstallOutcome(job);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '구성요소 자동 설치를 시작하지 못했습니다.');
    }
  }, [applyInstallOutcome, canInstall, missingComponents, onMessage]);

  return {
    load,
    install,
    installJob,
    isInstalling,
    canInstall,
    ready,
    missingComponents,
    ffmpegDependency,
    whisperStatus,
    autoInstallAvailable,
    autoInstallUnavailableReason,
    readinessTitle: ready ? '영상·음성·자막 저장 준비 완료' : '음성/자막 저장 준비가 필요합니다',
    readinessDetail: ready
      ? 'MP4, MP3, TXT를 바로 만들 수 있어요.'
      : autoInstallAvailable
        ? '필요한 항목만 앱에서 자동으로 준비할 수 있어요.'
        : '앱을 다시 설치하거나 개발 환경을 동기화한 뒤 다시 확인해 주세요.',
  };
}

export type MediaDependencies = ReturnType<typeof useMediaDependencies>;
