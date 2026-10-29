/**
 * Pure helpers of the grades tab: copy, error classification and number / time formatting.
 * Kept free of React and of the API client so Node can load it in the contract tests.
 * Nothing here logs or stores a grade value.
 */
import type { GradesClassDto, GradesRankDto, GradesSemesterDto, GradesSemesterKind } from './types';

export const GRADES_LABEL = '성적';
export const GRADES_CONSENT_SENTENCE = 'u-SAINT에 같은 계정으로 로그인해서 성적을 읽어 와요';
export const GRADES_CONSENT_TITLE = '성적 보기';
export const GRADES_CONSENT_POINTS = [
  '성적은 이 컴퓨터 안에서만 보여 주고, 기록(로그)에 남기지 않아요.',
  '성적을 바꾸거나 신청하는 일은 하지 않아요. 읽기만 해요.',
  '설정에서 언제든 끌 수 있어요.',
];
export const GRADES_CONSENT_ACCEPT = '동의하고 성적 보기';
export const GRADES_CONSENT_LATER = '나중에';
export const GRADES_RELEASE_NOTE = '이번 학기 성적은 학교가 공개한 뒤에 나타나요.';
export const GRADES_EMPTY_TEXT = '아직 확정된 성적이 없어요. 이번 학기 성적은 공개된 뒤에 보여요.';
export const GRADES_ERROR_TITLE = '지금 가져올 수 없어요';
export const GRADES_REFRESH_FAILED = '업데이트하지 못했어요';
export const GRADES_CONSENT_SAVE_FAILED = '동의를 저장하지 못했어요. 다시 시도해 주세요.';

/** Why the grades could not be read. All of them stay inside the grades tab. */
export type GradesFailureKind = 'unavailable' | 'login_rejected' | 'credentials_missing' | 'consent_required';

function codeOf(error: unknown): string | null {
  if (error && typeof error === 'object' && 'code' in error && typeof (error as { code: unknown }).code === 'string') {
    return (error as { code: string }).code;
  }
  return null;
}

/**
 * Sorts a failed grades call. Unknown codes, a 404 from an older backend, timeouts and any
 * HTTP status all mean "unavailable": the grades tab shows its own error and the app never
 * treats it as an LMS sign-out.
 */
export function classifyGradesError(error: unknown): GradesFailureKind {
  const code = codeOf(error);
  if (code === 'grades_login_rejected') return 'login_rejected';
  if (code === 'grades_consent_required') return 'consent_required';
  if (code === 'missing_credentials') return 'credentials_missing';
  return 'unavailable';
}

/** The one reason line under "지금 가져올 수 없어요". */
export function gradesFailureText(kind: GradesFailureKind): string {
  if (kind === 'login_rejected') return 'u-SAINT가 로그인을 받지 않았어요. u-SAINT 웹에서 직접 로그인되는지 확인해 주세요.';
  if (kind === 'credentials_missing') return '로그인 정보를 찾지 못했어요. 앱에 다시 로그인한 뒤 시도해 주세요.';
  return 'u-SAINT에 연결하지 못했어요. 강의 기능은 그대로 쓸 수 있어요.';
}

const SEMESTER_LABEL: Record<GradesSemesterKind, string> = {
  ONE: '1학기',
  SUMMER: '하계 계절학기',
  TWO: '2학기',
  WINTER: '동계 계절학기',
};

export function semesterLabel(year: number, semester: GradesSemesterKind): string {
  return `${year}년 ${SEMESTER_LABEL[semester] ?? String(semester)}`;
}

export function semesterKey(year: number, semester: GradesSemesterKind): string {
  return `${year}-${semester}`;
}

function finite(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Grade point average with two decimals, or a dash. */
export function formatGpa(value: number | null | undefined): string {
  return finite(value) ? value.toFixed(2) : '-';
}

/** Credits without a trailing ".0". */
export function formatCredits(value: number | null | undefined): string {
  if (!finite(value)) return '-';
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(1)));
}

export function formatRank(rank: GradesRankDto | null | undefined): string | null {
  if (!rank || !finite(rank.rank) || !finite(rank.total) || rank.total <= 0) return null;
  return `${rank.rank}/${rank.total}`;
}

/** "평점 3.85 · 취득 18학점 · 석차 12/340"; parts u-SAINT left empty are skipped. */
export function semesterStatsText(semester: GradesSemesterDto): string {
  const parts: string[] = [];
  if (finite(semester.grade_points_average)) parts.push(`평점 ${formatGpa(semester.grade_points_average)}`);
  if (finite(semester.earned_credits)) parts.push(`취득 ${formatCredits(semester.earned_credits)}학점`);
  const rank = formatRank(semester.semester_rank);
  if (rank) parts.push(`석차 ${rank}`);
  return parts.join(' · ');
}

/** Score cell: a number as text, P / F, or a dash. */
export function classScoreText(item: Pick<GradesClassDto, 'score'>): string {
  const score = item.score?.trim();
  return score ? score : '-';
}

/** "3분 전 업데이트"; empty when the time is missing or unreadable. */
export function updatedAgoText(fetchedAt: string | null | undefined, now: Date): string {
  if (!fetchedAt) return '';
  const time = Date.parse(fetchedAt);
  if (Number.isNaN(time)) return '';
  const minutes = Math.max(0, Math.floor((now.getTime() - time) / 60_000));
  if (minutes < 1) return '방금 업데이트';
  if (minutes < 60) return `${minutes}분 전 업데이트`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전 업데이트`;
  return `${Math.floor(hours / 24)}일 전 업데이트`;
}
