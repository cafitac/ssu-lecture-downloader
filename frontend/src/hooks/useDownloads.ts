import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, cancelDownloadJob, fetchDownloadJob, isAuthenticationError, startArtifactDownloadJob, startBatchDownloadJob, startDownloadJob } from '../lib/api';
import { SESSION_EXPIRED_MESSAGE, friendlyError } from '../lib/errors';
import { buildJobRows, holdFurthestRows, isExpiredMediaUrlCode, isRetryable, readPipelineSummary, summarizeRows } from '../lib/jobItems';
import type { JobPlan, JobRow } from '../lib/jobItems';
import { TERMINAL_DOWNLOAD_STATUSES } from '../lib/progress';
import type { DownloadJobStatus } from '../lib/types';
import { usePolledJob } from './usePolledJob';

interface Options {
  downloadDir: string;
  onAuthError: (message: string) => void;
  onError: (message: string) => void;
}

const isTerminal = (job: DownloadJobStatus | null) => Boolean(job && TERMINAL_DOWNLOAD_STATUSES.has(job.status));

/**
 * One download job at a time. Tracks the plan (what the user selected), the polled job,
 * the per-item rows for the drawer, and which items were saved during this session.
 */
export function useDownloads({ downloadDir, onAuthError, onError }: Options) {
  const [plan, setPlan] = useState<JobPlan | null>(null);
  const [job, setJob] = useState<DownloadJobStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState<ReadonlySet<string>>(new Set());
  const planRef = useRef<JobPlan | null>(null);
  // One automatic re-plan per user-started download when the CDN rejects a native transfer.
  const autoRetryUsedRef = useRef(false);
  const [autoRetryIds, setAutoRetryIds] = useState<string[] | null>(null);
  const callbacksRef = useRef({ onAuthError, onError });
  useEffect(() => {
    callbacksRef.current = { onAuthError, onError };
  });

  const active = starting || (job !== null && !isTerminal(job));
  // Furthest row seen per job id + row id: the backend drops old events past its cap, and a running row must not fall back.
  const furthestRef = useRef(new Map<string, JobRow>());
  const rows = useMemo<JobRow[]>(
    () => (plan ? holdFurthestRows(job?.job_id, buildJobRows(plan, job), furthestRef.current) : []),
    [plan, job],
  );
  const summary = useMemo(() => summarizeRows(rows, readPipelineSummary(job)), [rows, job]);

  /** Applies a fresh job snapshot; returns false when the failure sends the user back to login. */
  const applyJob = useCallback((next: DownloadJobStatus): boolean => {
    const native = next.job_id.startsWith('native-');
    if (
      next.status === 'failed'
      && next.error_code
      && !(native && isExpiredMediaUrlCode(next.error_code))
      && isAuthenticationError(new ApiError(next.error_code, next.error_code))
    ) {
      callbacksRef.current.onAuthError(SESSION_EXPIRED_MESSAGE);
      return false;
    }
    setJob((current) => (current === null || current.job_id === next.job_id ? next : current));
    if (isTerminal(next) && planRef.current) {
      const current = planRef.current;
      const finalRows = buildJobRows(current, next);
      if (native && !autoRetryUsedRef.current) {
        const expired = finalRows.filter((row) => row.phase === 'failed' && isExpiredMediaUrlCode(row.reasonCode ?? next.error_code));
        if (expired.length > 0) {
          autoRetryUsedRef.current = true;
          setAutoRetryIds(expired.map((row) => row.id));
        }
      }
      const finished = finalRows.filter((row) => row.phase === 'done');
      if (finished.length > 0) {
        setDownloaded((previous) => new Set([...previous, ...finished.map((row) => `${current.courseId}:${row.id}`)]));
      }
    }
    return true;
  }, []);

  usePolledJob(job, {
    fetch: fetchDownloadJob,
    isTerminal: (value) => TERMINAL_DOWNLOAD_STATUSES.has(value.status),
    onUpdate: (value) => {
      applyJob(value);
    },
    onError: (error) => {
      if (isAuthenticationError(error)) callbacksRef.current.onAuthError(friendlyError(error));
      else callbacksRef.current.onError(friendlyError(error, '진행 상황을 확인하지 못했어요.'));
    },
  });

  const start = useCallback(async (nextPlan: JobPlan, automatic = false): Promise<boolean> => {
    if (starting || (job !== null && !isTerminal(job)) || nextPlan.items.length === 0) return false;
    if (!automatic) autoRetryUsedRef.current = false;
    planRef.current = nextPlan;
    furthestRef.current.clear();
    setPlan(nextPlan);
    setJob(null);
    setStartError(null);
    setStarting(true);
    try {
      const ids = nextPlan.items.map((item) => item.rawId);
      const plainDownload = nextPlan.kind === 'material' || (nextPlan.outputs.length === 1 && nextPlan.outputs[0] === 'mp4');
      // Existing files are reused; the LMS is not asked twice for the same item.
      const started = plainDownload
        ? ids.length === 1
          ? await startDownloadJob(nextPlan.courseId, ids[0], nextPlan.courseName, downloadDir, 'skip')
          : await startBatchDownloadJob(nextPlan.courseId, nextPlan.courseName, ids, downloadDir, 'skip')
        : await startArtifactDownloadJob(nextPlan.courseId, nextPlan.courseName, ids, downloadDir, nextPlan.outputs, 'skip');
      return applyJob(started);
    } catch (error) {
      planRef.current = null;
      setPlan(null);
      if (isAuthenticationError(error)) {
        callbacksRef.current.onAuthError(friendlyError(error));
      } else {
        setStartError(friendlyError(error, '다운로드를 시작하지 못했어요.'));
      }
      return false;
    } finally {
      setStarting(false);
    }
  }, [applyJob, downloadDir, job, starting]);

  const cancel = useCallback(async () => {
    if (!job || isTerminal(job)) return;
    try {
      applyJob(await cancelDownloadJob(job.job_id));
    } catch (error) {
      if (isAuthenticationError(error)) callbacksRef.current.onAuthError(friendlyError(error));
      else callbacksRef.current.onError(friendlyError(error, '취소하지 못했어요.'));
    }
  }, [applyJob, job]);

  /** Runs the same plan again for the given rows (default: everything that did not finish). */
  const retry = useCallback(async (only?: string[]): Promise<boolean> => {
    if (!plan || active) return false;
    const wanted = new Set(only ?? rows.filter(isRetryable).map((row) => row.id));
    return start({ ...plan, items: plan.items.filter((item) => wanted.has(item.id)) });
  }, [active, plan, rows, start]);

  // Re-plan the CDN-rejected items once, outside the polling callback.
  useEffect(() => {
    if (!autoRetryIds || !plan || active) return;
    const wanted = new Set(autoRetryIds);
    setAutoRetryIds(null);
    void start({ ...plan, items: plan.items.filter((item) => wanted.has(item.id)) }, true);
  }, [active, autoRetryIds, plan, start]);

  const isDownloaded = useCallback((courseId: string | number, itemId: string) => downloaded.has(`${courseId}:${itemId}`), [downloaded]);

  return {
    plan,
    job,
    rows,
    summary,
    starting,
    active,
    startError,
    dismissStartError: () => setStartError(null),
    cancelRequested: Boolean(job?.cancel_requested),
    start,
    cancel,
    retry,
    isDownloaded,
  };
}

export type Downloads = ReturnType<typeof useDownloads>;
