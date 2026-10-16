import type { PageAttachments as Attachments } from '../hooks/usePageAttachments';
import { formatBytes } from '../lib/format';
import type { PageFile } from '../lib/types';
import { DownloadIcon } from './Icons';
import { Spinner } from './Screens';

interface Props {
  title: string;
  attachments: Attachments | undefined;
  onRetry: () => void;
  onSave: (file: PageFile) => void;
}

/** Files linked from one wiki page, listed under its row. */
export function PageAttachmentRows({ title, attachments, onRetry, onSave }: Props) {
  return (
    <div className="attachments" role="group" aria-label={`${title} 첨부 파일`}>
      {(!attachments || attachments.state === 'loading') && <p className="attachment-note" role="status"><Spinner />첨부 파일을 찾는 중…</p>}
      {attachments?.state === 'error' && (
        <p className="attachment-note" role="alert">
          {attachments.error}
          <button type="button" className="btn btn-sm" onClick={onRetry}>다시 시도</button>
        </p>
      )}
      {attachments?.state === 'ready' && attachments.files.length === 0 && <p className="attachment-note">이 페이지에는 받을 수 있는 첨부 파일이 없어요.</p>}
      {attachments?.state === 'ready' && attachments.files.map((file) => {
        const transfer = attachments.transfers[file.file_id];
        const saved = file.downloaded_path !== null && file.downloaded_path !== undefined;
        return (
          <div key={file.file_id} className="attachment-row">
            <span className="attachment-name" title={file.display_name}>{file.display_name}</span>
            <span className="cell-num">{formatBytes(file.size)}</span>
            <span className="cell-status">
              {saved && <span className="chip chip-info">받음</span>}
              {transfer === 'error' && <span className="chip chip-danger">실패</span>}
            </span>
            <button
              type="button"
              className="btn btn-sm"
              aria-label={`${file.display_name} 받기`}
              title={file.download_url ? (saved ? '이미 강의 폴더에 있어요' : '강의 폴더에 받기') : 'LMS에서 받을 수 없는 파일이에요'}
              disabled={file.download_url === null || transfer === 'busy' || saved}
              onClick={() => onSave(file)}
            >
              {transfer === 'busy' ? <Spinner /> : <DownloadIcon />}
              {transfer === 'busy' ? '받는 중' : '받기'}
            </button>
          </div>
        );
      })}
    </div>
  );
}
