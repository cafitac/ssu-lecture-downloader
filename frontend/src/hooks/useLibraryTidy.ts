import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, cancelTidyJob, fetchTidyJob, fetchTidyRuns, isAuthenticationError, startTidyApplyJob, startTidyPlanJob, startTidyUndoJob } from '../lib/api';
import { friendlyError } from '../lib/errors';
import { movingProgress, outcomeOf, undoableRun } from '../lib/tidy';
import type { TidyPhase } from '../lib/tidy';
import { TERMINAL_DOWNLOAD_STATUSES } from '../lib/progress';
import type { TidyJobKind, TidyJobStatus, TidyRun } from '../lib/types';
import { usePolledJob } from './usePolledJob';

interface Options {
  outputDir: string;
  /** A tidy-up (or undo) changed the folder: reload what shows saved files. */
  onChanged: () => void;
  onAuthError: (message: string) => void;
}

const isTerminal = (job: TidyJobStatus) => TERMINAL_DOWNLOAD_STATUSES.has(job.status);
const EXPIRED_NOTE = '미리보기가 오래돼서 다시 확인했어요.';

/**
 * The folder tidy-up dialog: preview (plan job) → move (apply job) → optional undo, each a backend
 * job polled to its end. `phase` is null while the dialog is closed.
 */
export function useLibraryTidy({ outputDir, onChanged, onAuthError }: Options) {
  const [phase, setPhase] = useState<TidyPhase | null>(null);
  const [job, setJob] = useState<{ kind: TidyJobKind; status: TidyJobStatus } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [runs, setRuns] = useState<{ state: 'loading' | 'ready' | 'unsupported'; list: TidyRun[] }>({ state: 'loading', list: [] });
  const callbacksRef = useRef({ onChanged, onAuthError });
  useEffect(() => {
    callbacksRef.current = { onChanged, onAuthError };
  });

  const refreshRuns = useCallback(async () => {
    try {
      setRuns({ state: 'ready', list: (await fetchTidyRuns(outputDir)).runs });
    } catch (error) {
      // A backend without the tidy-up answers 404: the entries stay hidden.
      setRuns({ state: error instanceof ApiError && error.code === 'HTTP 404' ? 'unsupported' : 'ready', list: [] });
    }
  }, [outputDir]);
  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  // A finished job can start a new preview (expired plan) or reload the run list: reached through refs, so no callback goes stale.
  const startPlanRef = useRef<() => Promise<void>>(async () => undefined);
  const refreshRunsRef = useRef(refreshRuns);
  useEffect(() => {
    refreshRunsRef.current = refreshRuns;
  });

  const settle = useCallback((kind: TidyJobKind, status: TidyJobStatus) => {
    if (status.status === 'failed' && status.error_code && isAuthenticationError(new ApiError(status.error_code, status.error_code))) {
      setPhase(null);
      setJob(null);
      callbacksRef.current.onAuthError(friendlyError(new ApiError(status.error_code, status.error_code)));
      return;
    }
    const outcome = outcomeOf(kind, status);
    if ('replan' in outcome) {
      setNote(EXPIRED_NOTE);
      void startPlanRef.current();
      return;
    }
    if (kind !== 'plan') {
      callbacksRef.current.onChanged();
      void refreshRunsRef.current();
    }
    if ('close' in outcome) {
      setPhase(null);
      setJob(null);
    } else {
      setPhase(outcome.phase);
    }
  }, []);

  const begin = useCallback(async (kind: TidyJobKind, start: () => Promise<TidyJobStatus>) => {
    try {
      const status = await start();
      setJob({ kind, status });
      if (isTerminal(status)) settle(kind, status);
      else if (kind !== 'plan') setPhase({ kind: 'moving', job: kind, ...movingProgress(status) });
    } catch (error) {
      setJob(null);
      if (error instanceof ApiError && error.code === 'tidy_plan_expired') {
        setNote(EXPIRED_NOTE);
        void startPlanRef.current();
      } else if (isAuthenticationError(error)) {
        setPhase(null);
        callbacksRef.current.onAuthError(friendlyError(error));
      } else {
        setPhase({ kind: 'error', message: friendlyError(error) });
      }
    }
  }, [settle]);

  const startPlan = useCallback(async () => {
    setPhase({ kind: 'checking' });
    await begin('plan', () => startTidyPlanJob(outputDir));
  }, [begin, outputDir]);
  useEffect(() => {
    startPlanRef.current = startPlan;
  });

  usePolledJob(job?.status ?? null, {
    fetch: (jobId) => fetchTidyJob(job?.kind ?? 'plan', jobId),
    isTerminal,
    onUpdate: (status) => {
      if (!job) return;
      setJob({ kind: job.kind, status });
      if (isTerminal(status)) settle(job.kind, status);
      else if (job.kind !== 'plan') setPhase({ kind: 'moving', job: job.kind, ...movingProgress(status) });
    },
    onError: (error) => {
      if (isAuthenticationError(error)) {
        setPhase(null);
        setJob(null);
        callbacksRef.current.onAuthError(friendlyError(error));
      } else {
        setPhase({ kind: 'error', message: friendlyError(error, '진행 상황을 확인하지 못했어요.') });
        setJob(null);
      }
    },
  });

  const open = useCallback(() => {
    setNote(null);
    void startPlan();
  }, [startPlan]);

  /** Applies the previewed plan: the plan id is single use, so an expired one previews again. */
  const apply = useCallback(() => {
    if (phase?.kind !== 'preview') return;
    setNote(null);
    setPhase({ kind: 'moving', job: 'apply', done: 0, total: phase.plan.move_count });
    void begin('apply', () => startTidyApplyJob(phase.plan.plan_id));
  }, [begin, phase]);

  /** "마지막 정리 되돌리기" (Settings) and "되돌리기" (result) both undo one run. */
  const undo = useCallback((runId?: string) => {
    const id = runId ?? undoableRun(runs.list)?.run_id;
    if (!id) return;
    setNote(null);
    setPhase({ kind: 'moving', job: 'undo', done: 0, total: 0 });
    void begin('undo', () => startTidyUndoJob(id));
  }, [begin, runs.list]);

  /** Stops after the file being moved; the job then reports what it did. */
  const stop = useCallback(async () => {
    if (!job || isTerminal(job.status)) return;
    try {
      const status = await cancelTidyJob(job.kind, job.status.job_id);
      setJob({ kind: job.kind, status });
      if (isTerminal(status)) settle(job.kind, status);
    } catch (error) {
      setPhase({ kind: 'error', message: friendlyError(error, '멈추지 못했어요.') });
    }
  }, [job, settle]);

  /** Closing is ignored while files are being moved; closing the preview check cancels it. */
  const close = useCallback(() => {
    if (phase?.kind === 'moving') return;
    if (phase?.kind === 'checking' && job && !isTerminal(job.status)) void cancelTidyJob(job.kind, job.status.job_id).catch(() => undefined);
    setPhase(null);
    setJob(null);
  }, [job, phase]);

  return {
    phase,
    note,
    runs,
    undoRun: undoableRun(runs.list),
    stopping: Boolean(job?.status.cancel_requested),
    /** Files are being moved: an app update must wait. */
    moving: phase?.kind === 'moving',
    open,
    apply,
    undo,
    stop,
    close,
  };
}

export type LibraryTidy = ReturnType<typeof useLibraryTidy>;
