import { isTauriRuntime } from '../lib/desktop';
import { contentKindLabel } from '../lib/format';
import { latestUserStage } from '../lib/progress';
import type { DownloadView } from '../lib/progress';
import type { CourseContentItem, CourseSummary, DownloadJobStatus, DownloadOutputFormat } from '../lib/types';
import type { MediaDependencies } from '../hooks/useMediaDependencies';
import { DownloadProgressCard } from './DownloadProgressCard';
import { WatchDebugPanel } from './WatchQueue';
import type { WatchDebugEvent } from './WatchQueue';

const OUTPUT_FORMAT_OPTIONS: Array<{ format: DownloadOutputFormat; title: string; detail: string }> = [
  { format: 'mp4', title: '영상', detail: 'MP4' },
  { format: 'mp3', title: '음성', detail: 'MP3' },
  { format: 'txt', title: '자막', detail: 'TXT' },
];

interface Props {
  selectedLecture: CourseContentItem | null;
  selectedCourse: CourseSummary | null;
  selectedCount: number;
  selectedOutputFormats: DownloadOutputFormat[];
  onToggleOutputFormat: (format: DownloadOutputFormat) => void;
  media: MediaDependencies;
  /** Components still missing for the currently selected outputs (empty for MP4 only). */
  missingSelectedOutputComponents: string[];
  downloadDir: string;
  onDownloadDirChange: (value: string) => void;
  onPickDownloadDir: () => void;
  isStartingDownload: boolean;
  isDownloadActive: boolean;
  downloadJob: DownloadJobStatus | null;
  canStartSelectedDownload: boolean;
  downloadPrimaryActionLabel: string;
  onPrimaryAction: () => void;
  watchDebugEvents: WatchDebugEvent[];
  view: DownloadView;
  downloadStartError: string | null;
  downloadDiagnostics: string[];
  isOpeningFolder: boolean;
  onOpenFolder: () => void;
}

export function DownloadPanel(props: Props) {
  const {
    selectedLecture, selectedCourse, selectedCount, selectedOutputFormats, media, missingSelectedOutputComponents,
    downloadDir, isStartingDownload, isDownloadActive, downloadJob, canStartSelectedDownload, downloadPrimaryActionLabel, view,
  } = props;
  const { installJob } = media;
  return (
    <section className="panel action-panel">
      <div className="panel-header">
        <h2>다운로드</h2>
        <span>{selectedLecture ? contentKindLabel(selectedLecture.kind) : 'media'}</span>
      </div>
      <div className="selected-box">
        <p className="label">다운로드할 강의</p>
        <strong>{selectedCount === 0 ? '강의를 선택하세요' : selectedCount === 1 ? '1개 선택됨' : `${selectedCount}개 선택됨`}</strong>
        <span>{selectedCourse?.name ?? '-'} · 하나만 선택하면 단일 다운로드, 2개 이상이면 최대 4개씩 동시 다운로드합니다.</span>
      </div>
      <div className="output-format-box">
        <p className="label">저장할 파일 선택</p>
        <div className="output-format-options" aria-label="저장할 파일 형식 다중 선택">
          {OUTPUT_FORMAT_OPTIONS.map(({ format, title, detail }) => (
            <label key={format} className="output-format-option">
              <input
                type="checkbox"
                checked={selectedOutputFormats.includes(format)}
                disabled={isStartingDownload || isDownloadActive}
                onChange={() => props.onToggleOutputFormat(format)}
              />
              <span>{title}</span>
              <small>{detail}</small>
            </label>
          ))}
        </div>
        <p className="hint">필요한 파일만 고르면 됩니다. 음성이나 자막을 고르면 영상 다운로드 후 자동으로 이어서 만들어 줍니다.</p>
        <div className={`media-readiness-card ${media.ready ? 'ready' : 'needs-action'}`}>
          <div className="media-readiness-copy">
            <strong>{media.readinessTitle}</strong>
            <span>{media.readinessDetail}</span>
          </div>
          <div className="media-readiness-actions">
            <button type="button" onClick={() => void media.load()} disabled={isStartingDownload || isDownloadActive || media.isInstalling}>다시 확인</button>
            {media.missingComponents.length > 0 && (
              <button type="button" className="install-action" onClick={() => void media.install()} disabled={!media.canInstall}>
                {media.isInstalling ? '준비 중...' : '자동 준비'}
              </button>
            )}
          </div>
        </div>
        {!media.ready && (
          <details className="dependency-details">
            <summary>자세한 준비 상태</summary>
            <div>
              <strong>음성 변환</strong>
              <span>{media.ffmpegDependency?.available ? '사용 가능' : '준비 필요'}</span>
            </div>
            <div>
              <strong>자막 생성</strong>
              <span>{media.whisperStatus?.available ? '사용 가능' : '준비 필요'}</span>
            </div>
          </details>
        )}
        {missingSelectedOutputComponents.length > 0 && <p className="progress-error">선택한 파일을 만들려면 먼저 음성/자막 저장 준비를 완료해 주세요.</p>}
        {installJob && <p className="save-location"><b>자동 준비</b> {installJob.status === 'succeeded' ? '완료' : installJob.status === 'running' ? '진행 중' : installJob.status} · {installJob.result?.message ?? installJob.error_code ?? latestUserStage(installJob.progress)}</p>}
      </div>
      <label className="download-dir-field">
        <span>다운로드 폴더 <small>자동 기본값</small></span>
        <div className="download-dir-control">
          <input
            name="downloadDir"
            value={downloadDir}
            onChange={(event) => props.onDownloadDirChange(event.target.value)}
            placeholder="~/Downloads"
            disabled={isStartingDownload || isDownloadActive}
          />
          <button type="button" onClick={props.onPickDownloadDir} disabled={!isTauriRuntime() || isStartingDownload || isDownloadActive}>Finder에서 선택</button>
        </div>
      </label>
      <p className="hint">선택하지 않아도 다운로드할 수 있습니다. Tauri 앱은 기본으로 macOS 다운로드 폴더인 ~/Downloads를 사용하고, 필요할 때 Finder에서 바꿀 수 있습니다.</p>
      <div className="actions">
        <button
          className={`download-primary ${isDownloadActive ? 'cancel' : ''}`}
          onClick={props.onPrimaryAction}
          disabled={isDownloadActive ? Boolean(downloadJob?.cancel_requested) : !canStartSelectedDownload}
        >
          {missingSelectedOutputComponents.length > 0 ? '구성요소 확인 필요' : downloadPrimaryActionLabel}
        </button>
      </div>
      <p className="hint">각 영상 행의 시청 버튼은 숭실/Commons 공식 플레이어 창을 열고, 이미 열린 시청 창이 있으면 새로 만들지 않고 앞으로 가져옵니다.</p>
      <WatchDebugPanel events={props.watchDebugEvents} />
      {view.shouldShowDownloadProgress && (
        <DownloadProgressCard
          view={view}
          downloadJob={downloadJob}
          downloadStartError={props.downloadStartError}
          downloadDiagnostics={props.downloadDiagnostics}
          isOpeningFolder={props.isOpeningFolder}
          onOpenFolder={props.onOpenFolder}
        />
      )}
    </section>
  );
}
