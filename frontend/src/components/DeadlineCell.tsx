import type { DeadlineInfo } from '../lib/deadline';

/** Badge (`D-3`, `기한 지남`, `10/27(화)부터`) plus the date; empty when there is nothing to show. */
export function DeadlineCell({ info }: { info: DeadlineInfo | null }) {
  return (
    <span className={`cell-deadline${info ? ` tone-${info.tone}` : ''}`} title={info?.title}>
      {info && <><span className="deadline-badge">{info.badge}</span>{info.when && <span className="deadline-when">{info.when}</span>}</>}
    </span>
  );
}
