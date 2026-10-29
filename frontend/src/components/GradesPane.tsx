import { useEffect, useRef } from 'react';
import type { ClassesEntry, GradesController } from '../hooks/useGrades';
import {
  GRADES_CERTIFICATED_NOTE,
  GRADES_CONSENT_ACCEPT,
  GRADES_CONSENT_LATER,
  GRADES_CONSENT_POINTS,
  GRADES_CONSENT_SENTENCE,
  GRADES_CONSENT_TITLE,
  GRADES_EMPTY_TEXT,
  GRADES_ERROR_TITLE,
  GRADES_LABEL,
  GRADES_REFRESH_FAILED,
  GRADES_RELEASE_NOTE,
  classScoreText,
  formatCredits,
  formatGpa,
  gradesFailureText,
  semesterKey,
  semesterLabel,
  semesterStatsText,
  updatedAgoText,
} from '../lib/grades';
import type { GradesClassDto, GradesSemesterDto, GradesSummaryDto } from '../lib/types';
import { ChevronIcon, RefreshIcon } from './Icons';
import { Spinner } from './Screens';

interface Props {
  grades: GradesController;
  now: Date;
  /** "나중에" on the consent step: back to the course view. */
  onLater: () => void;
}

function SummaryList({ summary, label }: { summary: GradesSummaryDto | null; label: string }) {
  return (
    <dl className="grades-summary-list" aria-label={label}>
      <div><dt>전체 평점</dt><dd>{formatGpa(summary?.grade_points_average)}</dd></div>
      <div><dt>취득 학점</dt><dd>{formatCredits(summary?.earned_credits)}</dd></div>
      <div><dt>신청 학점</dt><dd>{formatCredits(summary?.attempted_credits)}</dd></div>
    </dl>
  );
}

function ClassesTable({ classes, label }: { classes: GradesClassDto[]; label: string }) {
  return (
    <table className="grades-table">
      <caption className="visually-hidden">{label} 과목 성적</caption>
      <thead>
        <tr>
          <th scope="col">과목명</th>
          <th scope="col" className="is-num">학점</th>
          <th scope="col" className="is-num">등급</th>
          <th scope="col" className="is-num">점수</th>
          <th scope="col" className="col-professor">교수</th>
        </tr>
      </thead>
      <tbody>
        {classes.map((item) => (
          <tr key={item.code}>
            <th scope="row" className="grades-class-name" title={item.class_name}>{item.class_name}</th>
            <td className="is-num">{formatCredits(item.credits)}</td>
            <td className="is-num grades-grade">{item.grade?.trim() || '-'}</td>
            <td className="is-num">{classScoreText(item)}</td>
            <td className="col-professor" title={item.professor ?? undefined}>{item.professor?.trim() || '-'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SemesterClasses({ entry, label, onRetry }: { entry: ClassesEntry | undefined; label: string; onRetry: () => void }) {
  if (!entry || entry.state === 'loading') {
    return <p className="grades-classes-note" role="status"><Spinner />과목 성적을 불러오는 중…</p>;
  }
  if (entry.state === 'error') {
    return (
      <div className="grades-classes-note is-error" role="alert">
        <span>{entry.kind === 'login_rejected' ? 'u-SAINT가 로그인을 받지 않았어요.' : '과목 성적을 가져오지 못했어요.'}</span>
        <button type="button" className="btn btn-sm" onClick={onRetry}>다시 시도</button>
      </div>
    );
  }
  if (entry.classes.length === 0) return <p className="grades-classes-note">이 학기에 표시할 과목이 없어요.</p>;
  return <ClassesTable classes={entry.classes} label={label} />;
}

function SemesterGroup({ semester, open, entry, onToggle, onRetry }: {
  semester: GradesSemesterDto;
  open: boolean;
  entry: ClassesEntry | undefined;
  onToggle: () => void;
  onRetry: () => void;
}) {
  const label = semesterLabel(semester.year, semester.semester);
  const key = semesterKey(semester.year, semester.semester);
  const stats = semesterStatsText(semester);
  const count = entry?.state === 'ready' ? entry.classes.length : null;
  return (
    <section className="deadline-group" aria-label={label}>
      <h2 className="deadline-group-title grades-group-title">
        <button type="button" className="grades-group-toggle" aria-expanded={open} aria-controls={`grades-classes-${key}`} onClick={onToggle}>
          <ChevronIcon open={open} />
          <span className="grades-group-name">{label}</span>
          {count !== null && <span className="week-count">{count}과목</span>}
          {semester.academic_probation === true && <span className="chip chip-danger">학사경고</span>}
          {stats && <span className="grades-group-stats">{stats}</span>}
        </button>
      </h2>
      {open && (
        <div id={`grades-classes-${key}`} className="grades-classes">
          <SemesterClasses entry={entry} label={label} onRetry={onRetry} />
        </div>
      )}
    </section>
  );
}

/** "성적": u-SAINT grades after the user's agreement, in the same pane pattern as the deadlines. */
export function GradesPane({ grades, now, onLater }: Props) {
  const { phase, overview } = grades;
  const headingRef = useRef<HTMLHeadingElement>(null);

  // When the button the user pressed disappears (consent -> loading), keep the keyboard in the pane.
  useEffect(() => {
    // Never pull the keyboard out of an open dialog (e.g. Settings > 끄기 resets this pane underneath it).
    if (document.querySelector('[aria-modal="true"]')) return;
    const active = document.activeElement;
    if (!active || active === document.body) headingRef.current?.focus();
  }, [phase]);

  if (phase === 'consent') {
    return (
      <section className="lecture-pane grades-pane is-consent" aria-labelledby="grades-consent-title">
        <div className="grades-consent">
          <div className="center-card grades-consent-card">
            <h1 id="grades-consent-title" className="center-title" ref={headingRef} tabIndex={-1}>{GRADES_CONSENT_TITLE}</h1>
            <p className="center-sub">{GRADES_CONSENT_SENTENCE}.</p>
            <ul className="grades-consent-points">
              {GRADES_CONSENT_POINTS.map((point) => <li key={point}>{point}</li>)}
            </ul>
            {grades.consentError && <p className="notice notice-danger" role="alert">{grades.consentError}</p>}
            <div className="grades-consent-actions">
              <button type="button" className="btn btn-primary btn-lg" disabled={grades.consentBusy} onClick={() => void grades.grant()}>
                {grades.consentBusy ? <><Spinner />저장하는 중…</> : GRADES_CONSENT_ACCEPT}
              </button>
              <button type="button" className="btn btn-lg" disabled={grades.consentBusy} onClick={onLater}>{GRADES_CONSENT_LATER}</button>
            </div>
          </div>
        </div>
      </section>
    );
  }

  const ready = phase === 'ready' && overview !== null;
  const busy = phase === 'idle' || phase === 'checking' || phase === 'loading';
  const ago = ready ? updatedAgoText(overview.fetched_at, now) : '';
  const semesters = overview?.semesters ?? [];

  return (
    <section className={`lecture-pane grades-pane${ready && semesters.length > 0 ? ' has-note' : ''}`} aria-busy={busy || grades.refreshing}>
      <header className="pane-head">
        <div className="pane-head-row">
          <h1 className="pane-title" ref={headingRef} tabIndex={-1}>{GRADES_LABEL}</h1>
          {ready && (
            <button
              type="button"
              className="btn btn-icon btn-ghost"
              aria-label="성적 새로고침"
              title="성적 새로고침"
              disabled={grades.refreshing}
              onClick={grades.refresh}
            >
              <span className={grades.refreshing ? 'spin' : undefined}><RefreshIcon /></span>
            </button>
          )}
        </div>
        {ready && (
          <p className="pane-sub" aria-live="polite">
            {grades.refreshFailed ? (
              <>
                {GRADES_REFRESH_FAILED} · <button type="button" className="grades-inline-action" onClick={grades.refresh}>다시 시도</button>
              </>
            ) : (
              <>u-SAINT 기준{ago && ` · ${ago}`}</>
            )}
          </p>
        )}
      </header>
      {ready && semesters.length > 0 && <p className="deadline-note">{GRADES_RELEASE_NOTE}</p>}
      <div className="lecture-scroll">
        {busy && (
          <p className="pane-empty" role="status">
            <Spinner />{phase === 'loading' ? 'u-SAINT에서 성적을 불러오는 중…' : '성적 보기 상태를 확인하는 중…'}
          </p>
        )}
        {phase === 'error' && (
          <div className="pane-empty" role="alert">
            <p className="grades-error-title">{GRADES_ERROR_TITLE}</p>
            <p>{gradesFailureText(grades.reason)}</p>
            <button type="button" className="btn btn-sm" onClick={grades.retry}>다시 시도</button>
          </div>
        )}
        {ready && semesters.length === 0 && <p className="pane-empty">{GRADES_EMPTY_TEXT}</p>}
        {ready && semesters.length > 0 && (
          <>
            <section className="grades-summary" aria-label="전체 성적 요약">
              <p className="grades-summary-label">학적부 기준</p>
              <SummaryList summary={overview.recorded_summary} label="학적부 기준 요약" />
              {overview.certificated_summary && (
                <details className="grades-certificated">
                  <summary>증명 기준 보기</summary>
                  <p className="muted small">{GRADES_CERTIFICATED_NOTE}</p>
                  <SummaryList summary={overview.certificated_summary} label="증명 기준 요약" />
                </details>
              )}
            </section>
            {semesters.map((semester) => {
              const key = semesterKey(semester.year, semester.semester);
              return (
                <SemesterGroup
                  key={key}
                  semester={semester}
                  open={grades.expanded.has(key)}
                  entry={grades.classes[key]}
                  onToggle={() => grades.toggleSemester(semester)}
                  onRetry={() => grades.retryClasses(semester)}
                />
              );
            })}
          </>
        )}
      </div>
    </section>
  );
}
