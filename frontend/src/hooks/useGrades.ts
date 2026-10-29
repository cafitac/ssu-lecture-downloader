import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchGradeClasses, fetchGrades, fetchGradesStatus, grantGradesConsent, revokeGradesConsent } from '../lib/api';
import { GRADES_CONSENT_SAVE_FAILED, classifyGradesError, semesterKey } from '../lib/grades';
import type { GradesFailureKind } from '../lib/grades';
import type { GradesClassDto, GradesOverviewResponse, GradesSemesterDto } from '../lib/types';

/**
 * idle       - the tab was never opened (nothing was asked of the backend yet)
 * checking   - asking whether the user agreed
 * consent    - waiting for the user's agreement
 * loading    - first read from u-SAINT
 * ready      - grades shown (a refresh may run or have failed on top of them)
 * error      - "지금 가져올 수 없어요"
 */
export type GradesPhase = 'idle' | 'checking' | 'consent' | 'loading' | 'ready' | 'error';
/** What the settings dialog shows: not asked yet, asking, on, off, or the backend did not answer. */
export type GradesConsentState = 'unknown' | 'checking' | 'on' | 'off' | 'error';

export type ClassesEntry =
  | { state: 'loading' }
  | { state: 'ready'; classes: GradesClassDto[] }
  | { state: 'error'; kind: GradesFailureKind };

export type GradesController = ReturnType<typeof useGrades>;

/**
 * The grades tab's state. It lives only in memory (a page reload or sign-out drops it) and
 * has no `onAuthError`: a failure of u-SAINT, an old backend or a rejected login stays inside
 * the grades tab and never signs the user out of the LMS. Grade values are never logged.
 */
export function useGrades() {
  const [phase, setPhase] = useState<GradesPhase>('idle');
  const [consent, setConsent] = useState<GradesConsentState>('unknown');
  const [overview, setOverview] = useState<GradesOverviewResponse | null>(null);
  const [reason, setReason] = useState<GradesFailureKind>('unavailable');
  const [refreshing, setRefreshing] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [consentBusy, setConsentBusy] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);
  const [classes, setClasses] = useState<Readonly<Record<string, ClassesEntry>>>({});
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

  // Bumped whenever the data must be forgotten (turn off): late answers are dropped.
  const generation = useRef(0);
  const consentVersion = useRef(1);
  const overviewRef = useRef<GradesOverviewResponse | null>(null);
  const expandedRef = useRef<ReadonlySet<string>>(expanded);
  const phaseRef = useRef<GradesPhase>('idle');
  const classesRef = useRef(classes);
  useEffect(() => {
    classesRef.current = classes;
  });
  const setPhaseBoth = useCallback((next: GradesPhase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  const forget = useCallback(() => {
    generation.current += 1;
    overviewRef.current = null;
    expandedRef.current = new Set();
    setOverview(null);
    setClasses({});
    setExpanded(new Set());
    setRefreshing(false);
    setRefreshFailed(false);
  }, []);

  /** The backend says there is no (current) agreement: back to the consent step with no data. */
  const requireConsent = useCallback(() => {
    forget();
    setConsent('off');
    setConsentError(null);
    setPhaseBoth('consent');
  }, [forget, setPhaseBoth]);

  const loadClasses = useCallback(async (semester: GradesSemesterDto, refresh: boolean) => {
    const key = semesterKey(semester.year, semester.semester);
    const run = generation.current;
    setClasses((current) => ({ ...current, [key]: { state: 'loading' } }));
    try {
      const response = await fetchGradeClasses(semester.year, semester.semester, refresh);
      if (run !== generation.current) return;
      setClasses((current) => ({ ...current, [key]: { state: 'ready', classes: response.classes } }));
    } catch (error) {
      if (run !== generation.current) return;
      const kind = classifyGradesError(error);
      if (kind === 'consent_required') {
        requireConsent();
        return;
      }
      setClasses((current) => ({ ...current, [key]: { state: 'error', kind } }));
    }
  }, [requireConsent]);

  const loadOverview = useCallback(async (refresh: boolean) => {
    const run = generation.current;
    const hadOverview = overviewRef.current !== null;
    if (hadOverview) {
      setRefreshing(true);
      setRefreshFailed(false);
    } else {
      setPhaseBoth('loading');
    }
    try {
      const response = await fetchGrades(refresh);
      if (run !== generation.current) return;
      overviewRef.current = response;
      setOverview(response);
      setPhaseBoth('ready');
      if (refresh) {
        // The open semesters were read before; read them again so every shown number is current.
        const open = response.semesters.filter((semester) => expandedRef.current.has(semesterKey(semester.year, semester.semester)));
        for (const semester of open) {
          if (run !== generation.current) return;
          await loadClasses(semester, true);
        }
      }
      if (run === generation.current) setRefreshing(false);
    } catch (error) {
      if (run !== generation.current) return;
      const kind = classifyGradesError(error);
      if (kind === 'consent_required') {
        requireConsent();
        return;
      }
      if (hadOverview) {
        // Keep what is on screen; only the sub line says the update failed.
        setRefreshing(false);
        setRefreshFailed(true);
        return;
      }
      setReason(kind);
      setPhaseBoth('error');
    }
  }, [loadClasses, requireConsent, setPhaseBoth]);

  /** First step after the tab opens: ask whether the user agreed, then read or show the consent step. */
  const start = useCallback(async () => {
    const run = generation.current;
    setPhaseBoth('checking');
    try {
      const status = await fetchGradesStatus();
      if (run !== generation.current) return;
      consentVersion.current = status.current_consent_version || 1;
      if (!status.available) {
        setConsent(status.consent.granted ? 'on' : 'off');
        setReason('unavailable');
        setPhaseBoth('error');
        return;
      }
      if (status.consent.granted) {
        setConsent('on');
        await loadOverview(false);
      } else {
        setConsent('off');
        setPhaseBoth('consent');
      }
    } catch (error) {
      if (run !== generation.current) return;
      setReason(classifyGradesError(error));
      setPhaseBoth('error');
    }
  }, [loadOverview, setPhaseBoth]);

  /** Called when the grades tab is shown; does nothing unless it has never been opened. */
  const open = useCallback(() => {
    if (phaseRef.current === 'idle') void start();
  }, [start]);

  /** "다시 시도" in the error state. */
  const retry = useCallback(() => {
    void start();
  }, [start]);

  /** "동의하고 성적 보기": store the agreement, then read the grades. */
  const grant = useCallback(async () => {
    const run = generation.current;
    setConsentBusy(true);
    setConsentError(null);
    try {
      await grantGradesConsent(consentVersion.current);
      if (run !== generation.current) return;
      setConsent('on');
      setConsentBusy(false);
      await loadOverview(false);
    } catch {
      if (run !== generation.current) return;
      setConsentBusy(false);
      setConsentError(GRADES_CONSENT_SAVE_FAILED);
    }
  }, [loadOverview]);

  /** The refresh button, and "다시 시도" after a failed refresh. */
  const refresh = useCallback(() => {
    if (phaseRef.current !== 'ready') return;
    void loadOverview(true);
  }, [loadOverview]);

  const toggleSemester = useCallback((semester: GradesSemesterDto) => {
    const key = semesterKey(semester.year, semester.semester);
    const next = new Set(expandedRef.current);
    const opening = !next.has(key);
    if (opening) next.add(key);
    else next.delete(key);
    expandedRef.current = next;
    setExpanded(next);
    if (opening) {
      const entry = classesRef.current[key];
      if (!entry || entry.state === 'error') void loadClasses(semester, false);
    }
  }, [loadClasses]);
  const retryClasses = useCallback((semester: GradesSemesterDto) => {
    void loadClasses(semester, false);
  }, [loadClasses]);

  /** Settings: ask the backend about the agreement without touching u-SAINT. */
  const refreshStatus = useCallback(async () => {
    const run = generation.current;
    setConsent((current) => (current === 'unknown' ? 'checking' : current));
    try {
      const status = await fetchGradesStatus();
      if (run !== generation.current) return;
      consentVersion.current = status.current_consent_version || 1;
      setConsent(status.consent.granted ? 'on' : 'off');
    } catch {
      if (run !== generation.current) return;
      setConsent('error');
    }
  }, []);

  /** Settings "끄기": the backend deletes the agreement and its in-memory grades; so does the screen. */
  const revoke = useCallback(async (): Promise<string | null> => {
    try {
      await revokeGradesConsent();
    } catch {
      return '성적 보기를 끄지 못했어요. 잠시 후 다시 시도해 주세요.';
    }
    requireConsent();
    return null;
  }, [requireConsent]);

  return {
    phase,
    consent,
    overview,
    reason,
    refreshing,
    refreshFailed,
    consentBusy,
    consentError,
    classes,
    expanded,
    open,
    retry,
    grant,
    refresh,
    toggleSemester,
    retryClasses,
    refreshStatus,
    revoke,
  };
}
