import type { MediaComponent } from '../hooks/useMediaDependencies';
import type { LectureTab } from '../lib/content';
import type { DownloadOutputFormat } from '../lib/types';
import { AlertIcon, CheckIcon, CloseIcon, FolderIcon, ListIcon } from './Icons';
import { Spinner } from './Screens';

const FORMAT_OPTIONS: Array<{ format: DownloadOutputFormat; label: string; hint: string }> = [
  { format: 'mp4', label: '영상', hint: 'MP4 영상 파일' },
  { format: 'mp3', label: '음성', hint: 'MP3 음성 파일' },
  { format: 'txt', label: '자막', hint: 'TXT 자막 파일 (음성을 글로 옮겨요)' },
];

const COMPONENT_NAME: Record<MediaComponent, string> = { ffmpeg: '음성 변환', faster_whisper: '자막 생성' };

interface Props {
  tab: LectureTab;
  selectedCount: number;
  /** Total size text such as `7.0GB` / `약 7.0GB`, or `null` when no size is known. */
  sizeText: string | null;
  formats: DownloadOutputFormat[];
  formatsLocked: boolean;
  onToggleFormat: (format: DownloadOutputFormat) => void;
  missingComponents: MediaComponent[];
  installing: boolean;
  canInstall: boolean;
  installBlockedReason: string | null;
  onInstall: () => void;
  folderName: string;
  folderPath: string;
  onOpenSettings: () => void;
  progressLabel: string | null;
  drawerOpen: boolean;
  onToggleDrawer: () => void;
  starting: boolean;
  jobActive: boolean;
  canDownload: boolean;
  onDownload: () => void;
  startError: string | null;
  onDismissError: () => void;
}

export function ActionBar(props: Props) {
  const { tab, selectedCount, sizeText, formats, missingComponents, startError } = props;
  return (
    <footer className="action-bar">
      {startError && (
        <p className="notice notice-danger bar-notice" role="alert">
          <AlertIcon />
          <span>{startError}</span>
          <button type="button" className="btn btn-icon btn-ghost btn-sm" aria-label="닫기" onClick={props.onDismissError}><CloseIcon /></button>
        </p>
      )}
      {tab === 'video' && missingComponents.length > 0 && (
        <p className="notice notice-warning bar-notice" role="status">
          <AlertIcon />
          <span>{missingComponents.map((component) => COMPONENT_NAME[component]).join('·')} 구성요소를 먼저 설치해야 해요.</span>
          <button
            type="button"
            className="btn btn-sm"
            disabled={!props.canInstall}
            title={props.installBlockedReason ?? undefined}
            onClick={props.onInstall}
          >
            {props.installing ? <><Spinner />설치 중…</> : '설치'}
          </button>
        </p>
      )}
      <div className="bar-row">
        <div className="bar-summary" aria-live="polite">
          {selectedCount === 0 ? (
            <span className="muted">받을 항목을 선택하세요</span>
          ) : (
            <><strong>{selectedCount}개 선택</strong>{sizeText && <span className="muted"> · {sizeText}</span>}</>
          )}
        </div>
        {tab === 'video' && (
          <div className="format-group" role="group" aria-label="받을 파일 종류">
            {FORMAT_OPTIONS.map(({ format, label, hint }) => {
              const on = formats.includes(format);
              return (
                <button
                  key={format}
                  type="button"
                  className="format-toggle"
                  aria-pressed={on}
                  title={hint}
                  disabled={props.formatsLocked || (on && formats.length === 1)}
                  onClick={() => props.onToggleFormat(format)}
                >
                  {on && <CheckIcon />}{label}
                </button>
              );
            })}
          </div>
        )}
        <button type="button" className="btn btn-ghost folder-chip" title={`저장 폴더: ${props.folderPath}`} onClick={props.onOpenSettings}>
          <FolderIcon /><span>{props.folderName}</span>
        </button>
        {props.progressLabel !== null && (
          <button type="button" className="btn btn-ghost" aria-expanded={props.drawerOpen} onClick={props.onToggleDrawer}>
            <ListIcon />진행 상황{props.progressLabel && <span className="muted"> {props.progressLabel}</span>}
          </button>
        )}
        <button type="button" className="btn btn-primary btn-lg" disabled={!props.canDownload} onClick={props.onDownload}>
          {props.starting ? <><Spinner />준비 중…</> : props.jobActive ? <><Spinner />받는 중…</> : '다운로드'}
        </button>
      </div>
    </footer>
  );
}
