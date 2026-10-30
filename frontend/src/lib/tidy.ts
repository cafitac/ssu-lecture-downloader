/**
 * Folder tidy-up: the dialog's states and the copy it shows (design/09 "화면과 문구").
 * Pure, so Node can load it in the contract tests; the hook only wires it to the API.
 */
import { knownErrorText, GENERIC_ERROR } from './errors.ts';
import type { TidyApplyResult, TidyJobKind, TidyJobStatus, TidyMove, TidyPlan, TidyRun, TidyUndoResult } from './types';

export type TidyPhase =
  | { kind: 'checking' }
  | { kind: 'preview'; plan: TidyPlan }
  | { kind: 'empty' }
  | { kind: 'moving'; job: 'apply' | 'undo'; done: number; total: number }
  | { kind: 'result'; result: TidyApplyResult }
  | { kind: 'undone'; result: TidyUndoResult }
  | { kind: 'error'; message: string };

/** What a finished job leads to: the next dialog state, a fresh preview, or closing the dialog. */
export type TidyOutcome = { phase: TidyPhase } | { replan: true } | { close: true };

export const TIDY_COPY = {
  notice: (count: number) => `이 과목에 예전 방식으로 저장된 파일이 ${count}개 있어요. 주차·강의별 폴더로 정리할 수 있어요.`,
  busyHint: '다운로드가 끝나면 정리할 수 있어요',
  checking: '옮길 파일을 확인하고 있어요…',
  preview: (count: number) => `파일 ${count}개를 주차·강의별 폴더로 옮겨요. 지우거나 덮어쓰는 파일은 없어요.`,
  openFiles: '열려 있는 파일도 옮겨져요. 재생 중인 영상은 계속 재생되지만, 다시 열 때는 새 폴더에서 열어 주세요.',
  skipsSummary: (count: number) => `그대로 두는 파일 ${count}개`,
  skipsSummaryAfter: (count: number) => `그대로 둔 파일 ${count}개`,
  empty: '정리할 파일이 없어요.',
  moving: (done: number, total: number) => `정리하는 중… ${done}/${total}`,
  undoing: (done: number, total: number) => (total > 0 ? `되돌리는 중… ${done}/${total}` : '되돌리는 중…'),
  reconnecting: '연결 확인 중…',
  stopHint: '지금 파일까지만 옮기고 멈춰요',
  result: (moved: number, skipped: number) => `${moved}개를 옮겼어요.${skipped > 0 ? ` ${skipped}개는 그대로 뒀어요.` : ''}`,
  undone: (restored: number, skipped: number) => `${restored}개를 원래 자리로 돌려놨어요.${skipped > 0 ? ` ${skipped}개는 그대로 뒀어요.` : ''}`,
  more: (count: number) => `${count}개 더 있어요`,
} as const;

const SKIP_REASONS: Record<string, string> = {
  destination_exists: '같은 이름의 파일이 이미 있어요',
  unknown_file: '이 앱이 받은 파일인지 알 수 없어요',
  partial_download: '받다 만 파일이에요',
  course_not_matched: '이번 학기 과목 목록에 없는 폴더예요',
  duplicate_transcript: '같은 강의의 자막 글이 이미 있어요 (예전 파일)',
  source_changed: '미리보기 뒤에 파일이 바뀌었어요',
  source_missing: '미리보기 뒤에 파일이 없어졌어요',
  not_a_regular_file: '폴더나 바로가기는 옮기지 않아요',
  in_use: '다른 앱이 쓰고 있어요 (Windows)',
  path_too_long: '경로가 너무 길어요 (Windows)',
  move_failed: '옮기지 못했어요',
};

// An undo leaves a file where it is for other reasons than "the preview is old".
const UNDO_REASONS: Record<string, string> = {
  source_changed: '정리한 뒤에 파일이 바뀌어서 그대로 뒀어요',
  source_missing: '정리한 뒤에 파일이 없어져서 그대로 뒀어요',
};

/** Why a file stays where it is; a code this version does not know still gets a plain sentence. */
export function skipReasonText(code: string, context: 'plan' | 'undo' = 'plan'): string {
  if (context === 'undo' && Object.hasOwn(UNDO_REASONS, code)) return UNDO_REASONS[code];
  return Object.hasOwn(SKIP_REASONS, code) ? SKIP_REASONS[code] : '옮기지 않고 그대로 뒀어요';
}

/** Last path segment: the dialog lists file names, not the user's folders. */
export function fileName(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : path;
}

/** A long list shows its first `cap` rows and says how many more there are. */
export function capRows<T>(rows: readonly T[], cap: number): { shown: T[]; hidden: number } {
  return { shown: rows.slice(0, cap), hidden: Math.max(0, rows.length - cap) };
}

export interface TidyLectureGroup {
  /** `null`: materials, listed straight under the week. */
  stem: string | null;
  moves: TidyMove[];
}
export interface TidyWeekGroup {
  week: string;
  lectures: TidyLectureGroup[];
}
export interface TidyCourseGroup {
  course: string;
  weeks: TidyWeekGroup[];
}

/** Groups moves course › week › lecture, keeping the order the backend planned them in. */
export function groupMoves(moves: readonly TidyMove[]): TidyCourseGroup[] {
  const courses: TidyCourseGroup[] = [];
  for (const move of moves) {
    let course = courses.find((candidate) => candidate.course === move.course);
    if (!course) courses.push((course = { course: move.course, weeks: [] }));
    let week = course.weeks.find((candidate) => candidate.week === move.week_folder);
    if (!week) course.weeks.push((week = { week: move.week_folder, lectures: [] }));
    let lecture = week.lectures.find((candidate) => candidate.stem === move.lecture_stem);
    if (!lecture) week.lectures.push((lecture = { stem: move.lecture_stem, moves: [] }));
    lecture.moves.push(move);
  }
  return courses;
}

/** The progress of a running move: the newest `tidy_moving` event, else what was known before (no "0/0" flash). */
export function movingProgress(job: TidyJobStatus, before: { done: number; total: number } = { done: 0, total: 0 }): { done: number; total: number } {
  for (let index = job.progress.length - 1; index >= 0; index -= 1) {
    const event = job.progress[index];
    if (event.stage === 'tidy_moving') return { done: event.done ?? 0, total: event.total ?? before.total };
  }
  return before;
}

/** The course notice: shown for old files, unless answered with 나중에, nothing is left to tidy, or the count is unknown. */
export function legacyNoticeState(count: number | null, answeredLater: boolean, nothingToTidy: boolean, blocked: boolean): { count: number; blocked: boolean } | null {
  return count !== null && count > 0 && !answeredLater && !nothingToTidy ? { count, blocked } : null;
}

/** The first dialog state after a finished plan job. */
export function phaseForPlan(plan: TidyPlan): TidyPhase {
  return plan.move_count > 0 ? { kind: 'preview', plan } : { kind: 'empty' };
}

/**
 * What a terminal job means for the dialog. An expired preview is re-checked on its own; a stopped
 * move still reports what it moved; a stopped preview just closes.
 */
export function outcomeOf(kind: TidyJobKind, job: TidyJobStatus): TidyOutcome {
  if (job.error_code === 'tidy_plan_expired') return { replan: true };
  if (job.status === 'failed') return { phase: { kind: 'error', message: knownErrorText(job.error_code) ?? GENERIC_ERROR } };
  if (!job.result) return job.status === 'cancelled' ? { close: true } : { phase: { kind: 'error', message: GENERIC_ERROR } };
  if (kind === 'plan') return { phase: phaseForPlan(job.result as TidyPlan) };
  if (kind === 'apply') return { phase: { kind: 'result', result: job.result as TidyApplyResult } };
  return { phase: { kind: 'undone', result: job.result as TidyUndoResult } };
}

/** The run "마지막 정리 되돌리기" would undo: the newest run, and only when it can still be undone. */
export function undoableRun(runs: readonly TidyRun[]): TidyRun | null {
  return runs.length > 0 && runs[0].undo_available ? runs[0] : null;
}

/** A backend that predates the tidy-up answers unknown routes with 404 / `not_found`. */
export function isUnsupportedBackend(code: string): boolean {
  return code === 'HTTP 404' || code === 'not_found';
}
