import { isTauriRuntime } from '../lib/desktop';
import { formatBytes, progressLabelPercent } from '../lib/format';
import type { DownloadView } from '../lib/progress';
import type { DownloadJobStatus } from '../lib/types';

interface Props {
  view: DownloadView;
  downloadJob: DownloadJobStatus | null;
  downloadStartError: string | null;
  downloadDiagnostics: string[];
  isOpeningFolder: boolean;
  onOpenFolder: () => void;
}

function PhaseRow({ className, title, detail, label, percent }: { className: string; title: string; detail: string; label: string; percent: number }) {
  return (
    <div className={`phase-progress-row ${className}`}>
      <div className="phase-progress-title">
        <strong>{title}</strong>
        <span>{detail}</span>
      </div>
      <div className="progress-track phase-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <div className="progress-fill" style={{ width: progressLabelPercent(percent) }} />
      </div>
    </div>
  );
}

export function DownloadProgressCard({ view, downloadJob, downloadStartError, downloadDiagnostics, isOpeningFolder, onOpenFolder }: Props) {
  const {
    activeSingleResult, batchPrepareProgress, batchDownloadInfo, artifactAudioInfo, artifactTranscriptInfo, perFileProgress,
    downloadProgress, aggregateProgress, downloadProgressPercent, batchDownloadPhasePercent, batchDownloadPhaseLabel,
    downloadStatusLabel, downloadProgressMessage, responseDiagnostics, artifactErrorText, downloadOutputPath, canOpenDownloadedFolder,
  } = view;
  const showPhases = batchPrepareProgress.total > 1 || artifactAudioInfo.started || artifactTranscriptInfo.started;
  return (
    <div className="download-progress-card" aria-live="polite">
      <div className="progress-card-header">
        <div>
          <p className="label">다운로드 진행률</p>
          <strong>{downloadStatusLabel}</strong>
        </div>
        <span>{progressLabelPercent(downloadProgressPercent)}</span>
      </div>
      <div
        className="progress-track"
        role="progressbar"
        aria-label="다운로드 진행률"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={downloadProgressPercent}
      >
        <div className="progress-fill" style={{ width: `${downloadProgressPercent}%` }} />
      </div>
      <p className="progress-message">{downloadProgressMessage}</p>
      {showPhases && (
        <div className="phase-progress-list" aria-label="다운로드 단계">
          {batchPrepareProgress.total > 1 && (
            <PhaseRow
              className={batchPrepareProgress.done ? 'done' : 'active'}
              title="1. 파일 위치 확인"
              detail={`${batchPrepareProgress.resolved}/${batchPrepareProgress.total}`}
              label="파일 위치 확인 진행률"
              percent={batchPrepareProgress.percent}
            />
          )}
          {batchPrepareProgress.total > 1 && (
            <PhaseRow
              className={batchDownloadInfo.skipped ? 'done' : batchDownloadInfo.started ? 'active' : ''}
              title="2. 실제 파일 동시 다운로드"
              detail={batchDownloadPhaseLabel}
              label="실제 파일 다운로드 진행률"
              percent={batchDownloadPhasePercent}
            />
          )}
          {artifactAudioInfo.started && (
            <PhaseRow
              className={artifactAudioInfo.done ? 'done' : 'active'}
              title={`${batchPrepareProgress.total > 1 ? '3' : '2'}. MP3 변환`}
              detail={`${artifactAudioInfo.current}/${artifactAudioInfo.total}`}
              label="MP3 변환 진행률"
              percent={artifactAudioInfo.percent}
            />
          )}
          {artifactTranscriptInfo.started && (
            <PhaseRow
              className={artifactTranscriptInfo.done ? 'done' : 'active'}
              title={`${batchPrepareProgress.total > 1 ? '4' : artifactAudioInfo.started ? '3' : '2'}. TXT 생성`}
              detail={`${artifactTranscriptInfo.current}/${artifactTranscriptInfo.total}`}
              label="TXT 생성 진행률"
              percent={artifactTranscriptInfo.percent}
            />
          )}
        </div>
      )}
      <p className="save-location"><b>저장 위치</b> {downloadOutputPath}</p>
      {(aggregateProgress.written || downloadProgress.written || activeSingleResult?.download_size) && (
        <p className="progress-size">
          {formatBytes(aggregateProgress.written ?? downloadProgress.written ?? activeSingleResult?.download_size)} / {formatBytes(aggregateProgress.total ?? downloadProgress.total ?? activeSingleResult?.download_size)}
        </p>
      )}
      {responseDiagnostics && <p className="progress-diagnostics">응답 진단: {responseDiagnostics}</p>}
      {perFileProgress.length > 0 && (
        <details className="file-progress-details" open={perFileProgress.length <= 5}>
          <summary>파일별 진행률 {perFileProgress.filter((row) => row.percent >= 100).length}/{perFileProgress.length}</summary>
          <div className="file-progress-list">
            {perFileProgress.map((row) => (
              <div className={`file-progress-row ${row.status === '실패' ? 'failed' : ''}`} key={row.key}>
                <div className="file-progress-title">
                  <strong>{row.title}</strong>
                  <span>{row.status} · {progressLabelPercent(row.percent)}</span>
                </div>
                <div
                  className="progress-track file-track"
                  role="progressbar"
                  aria-label={`${row.title} 진행률`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={row.percent}
                >
                  <div className="progress-fill" style={{ width: progressLabelPercent(row.percent) }} />
                </div>
                <p className="file-progress-meta">
                  {formatBytes(row.written)} / {formatBytes(row.total)}{row.outputPath ? ` · ${row.outputPath}` : ''}{row.errorCode ? ` · ${row.errorCode}` : ''}
                </p>
              </div>
            ))}
          </div>
        </details>
      )}
      {artifactErrorText && <p className="progress-error">파일 생성 실패: {artifactErrorText}</p>}
      {downloadStartError && !downloadJob && <p className="progress-error">다운로드 시작 실패: {downloadStartError}</p>}
      {downloadJob?.error_code && <p className="progress-error">다운로드에 실패했습니다. 다시 시도하거나 저장 위치를 확인하세요. ({downloadJob.error_code})</p>}
      {downloadDiagnostics.length > 0 && (
        <details className="file-progress-details" open={Boolean(downloadStartError && !downloadJob)}>
          <summary>다운로드 진단 로그 {downloadDiagnostics.length}개</summary>
          <div className="file-progress-list">
            {downloadDiagnostics.map((line, index) => (
              <p className="file-progress-meta" key={`${line}-${index}`}>{line}</p>
            ))}
          </div>
        </details>
      )}
      {canOpenDownloadedFolder && (
        <button type="button" className="open-folder-button" onClick={onOpenFolder} disabled={!isTauriRuntime() || isOpeningFolder}>
          {isOpeningFolder ? '여는 중...' : '다운로드 폴더 열기'}
        </button>
      )}
    </div>
  );
}
