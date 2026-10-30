import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, cancelTidyJob, fetchTidyJob, fetchTidyRuns, isAuthenticationError, startTidyApplyJob, startTidyPlanJob, startTidyUndoJob } from '../lib/api';
import { friendlyError } from '../lib/errors';
import { TERMINAL_DOWNLOAD_STATUSES } from '../lib/progress';
import { isUnsupportedBackend, movingProgress, outcomeOf, undoableRun } from '../lib/tidy';
import type { TidyPhase } from '../lib/tidy';
import type { TidyJobKind, TidyJobStatus, TidyRun } from '../lib/types';
import { usePolledJob } from './usePolledJob';

interface Options {
  outputDir: string;
  /** A tidy-up (or undo) changed the folder: reload what shows saved files. */
  onChanged: () => void;
  /** A preview found nothing to move: the "예전 방식 파일" notice no longer applies. */
  onNothingToTidy: () => void;
  onAuthError: (message: string) => void;
}

interface ActiveJob {
  /** Which start this job belongs to; `close()` and every new start move the counter on. */
  gen: number;
  kind: TidyJobKind;
  status: TidyJobStatus;
}

const isTerminal = (job: TidyJobStatus) => TERMINAL_DOWNLOAD_STATUSES.has(job.status);
const EXPIRED_NOTE = '미리보기가 오래돼서 다시 확인했어요.';
const NONE = { done: 0, total: 0 };

/**
 * The folder tidy-up dialog: preview (plan job) → move (apply job) → optional undo, each a backend
 * job polled to its end. `phase` is null while the dialog is closed. Files already being moved are
 * never abandoned by the UI: a failed poll keeps the job, only a lost job or a lost login ends it.
 */
export function useLibraryTidy({ outputDir, onChanged, onNothingToTidy, onAuthError }: Options) {
  const [phase, setPhase] = useState<TidyPhase | null>(null);
  const [job, setJob] = useState<ActiveJob | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [runs, setRuns] = useState<{ state: 'loading' | 'ready' | 'unsupported'; list: TidyRun[] }>({ state: 'loading', list: [] });
  const callbacksRef = useRef({ onChanged, onNothingToTidy, onAuthError });
  const genRef = useRef(0);
  const progressRef = useRef(NONE);
  // A 멈추기 pressed before the backend named the job waits here and is sent once it did.
  const stopQueuedRef = useRef(false);
  useEffect(() => {
    callbacksRef.current = { onChanged, onNothingToTidy, onAuthError };
  });

  const refreshRuns = useCallback(async () => {
    try {
      setRuns({ state: 'ready', list: (await fetchTidyRuns(outputDir)).runs });
    } catch (error) {
      // A backend without the tidy-up answers an unknown route: the entries stay hidden.
      setRuns({ state: error instanceof ApiError && isUnsupportedBackend(error.code) ? 'unsupported' : 'ready', list: [] });
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

  const giveUp = useCallback(() => {
    setPhase(null);
    setJob(null);
    setReconnecting(false);
  }, []);

  const settle = useCallback((kind: TidyJobKind, status: TidyJobStatus) => {
    setReconnecting(false);
    if (status.status === 'failed' && status.error_code && isAuthenticationError(new ApiError(status.error_code, status.error_code))) {
      giveUp();
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
      giveUp();
    } else {
      if (outcome.phase.kind === 'empty') callbacksRef.current.onNothingToTidy();
      setPhase(outcome.phase);
    }
  }, [giveUp]);

  const sendStop = useCallback(async (kind: TidyJobKind, jobId: string, gen: number) => {
    setStopRequested(true);
    try {
      const status = await cancelTidyJob(kind, jobId);
      if (gen !== genRef.current) return;
      setJob({ gen, kind, status });
      if (isTerminal(status)) settle(kind, status);
    } catch {
      // The move goes on; the button works again.
      if (gen === genRef.current) setStopRequested(false);
    }
  }, [settle]);

  const begin = useCallback(async (kind: TidyJobKind, start: () => Promise<TidyJobStatus>, before = NONE) => {
    const gen = ++genRef.current;
    stopQueuedRef.current = false;
    progressRef.current = before;
    setJob(null);
    setStopRequested(false);
    setReconnecting(false);
    try {
      const status = await start();
      if (gen !== genRef.current) {
        // The dialog was closed while the backend was still starting this job.
        if (!isTerminal(status)) void cancelTidyJob(kind, status.job_id).catch(() => undefined);
        return;
      }
      setJob({ gen, kind, status });
      if (isTerminal(status)) {
        settle(kind, status);
        return;
      }
      if (kind !== 'plan') {
        progressRef.current = movingProgress(status, progressRef.current);
        setPhase({ kind: 'moving', job: kind, ...progressRef.current });
      }
      if (stopQueuedRef.current) await sendStop(kind, status.job_id, gen);
    } catch (error) {
      if (gen !== genRef.current) return;
      setJob(null);
      if (kind === 'undo') void refreshRunsRef.current();
      if (error instanceof ApiError && error.code === 'tidy_plan_expired') {
        setNote(EXPIRED_NOTE);
        void startPlanRef.current();
      } else if (isAuthenticationError(error)) {
        giveUp();
        callbacksRef.current.onAuthError(friendlyError(error));
      } else {
        setPhase({ kind: 'error', message: friendlyError(error) });
      }
    }
  }, [giveUp, sendStop, settle]);

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
      if (!job || job.gen !== genRef.current) return;
      setReconnecting(false);
      setJob({ gen: job.gen, kind: job.kind, status });
      if (isTerminal(status)) {
        settle(job.kind, status);
      } else if (job.kind !== 'plan') {
        progressRef.current = movingProgress(status, progressRef.current);
        setPhase({ kind: 'moving', job: job.kind, ...progressRef.current });
      }
    },
    onError: (error) => {
      if (isAuthenticationError(error)) {
        giveUp();
        callbacksRef.current.onAuthError(friendlyError(error));
      } else if (error instanceof ApiError && error.code === 'tidy_job_not_found') {
        setJob(null);
        setPhase({ kind: 'error', message: friendlyError(error) });
      } else {
        // A hiccup (sleep, a slow backend): the job runs on, polling continues.
        setReconnecting(true);
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
    const before = { done: 0, total: phase.plan.move_count };
    setPhase({ kind: 'moving', job: 'apply', ...before });
    void begin('apply', () => startTidyApplyJob(phase.plan.plan_id), before);
  }, [begin, phase]);

  /** "마지막 정리 되돌리기" (Settings) and "되돌리기" (result) both undo one run. */
  const undo = useCallback((runId?: string) => {
    const id = runId ?? undoableRun(runs.list)?.run_id;
    if (!id) return;
    setNote(null);
    setPhase({ kind: 'moving', job: 'undo', ...NONE });
    void begin('undo', () => startTidyUndoJob(id, outputDir));
  }, [begin, outputDir, runs.list]);

  /** Stops after the file being moved; the job then reports what it did. */
  const stop = useCallback(async () => {
    if (phase?.kind !== 'moving' || stopRequested) return;
    if (!job) {
      stopQueuedRef.current = true;
      setStopRequested(true);
      return;
    }
    if (!isTerminal(job.status)) await sendStop(job.kind, job.status.job_id, job.gen);
  }, [job, phase, sendStop, stopRequested]);

  /** Closing is ignored while files are being moved; closing the preview check cancels it. */
  const close = useCallback(() => {
    if (phase?.kind === 'moving') return;
    genRef.current += 1;
    if (phase?.kind === 'checking' && job && !isTerminal(job.status)) void cancelTidyJob(job.kind, job.status.job_id).catch(() => undefined);
    setPhase(null);
    setJob(null);
    setReconnecting(false);
  }, [job, phase]);

  return {
    phase,
    note,
    runs,
    undoRun: undoableRun(runs.list),
    stopping: stopRequested,
    reconnecting,
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
