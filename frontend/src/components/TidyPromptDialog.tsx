import { TIDY_PROMPT_TITLE, tidyPromptSentence } from '../lib/tidyPrompt';
import { Dialog } from './Dialog';

interface Props {
  count: number;
  onPreview: () => void;
  onLater: () => void;
}

/** "파일 정리": asked once after an update when old-style files exist. 미리보기 moves nothing; the tidy-up dialog asks again. */
export function TidyPromptDialog({ count, onPreview, onLater }: Props) {
  return (
    <Dialog title={TIDY_PROMPT_TITLE} onClose={onLater}>
      <section className="tidy-prompt">
        <p className="tidy-prompt-lead">{tidyPromptSentence(count)}</p>
        <div className="setting-actions tidy-prompt-actions">
          <button type="button" className="btn btn-sm" onClick={onLater}>나중에</button>
          <button type="button" className="btn btn-sm btn-primary" onClick={onPreview}>미리보기</button>
        </div>
      </section>
    </Dialog>
  );
}
