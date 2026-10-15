import type { DownloadJobStatus, ProgressEvent } from './types';

export interface PostprocessEntry {
  /** Live progress; the post-process task appends to it while pollers read copies. */
  progress: ProgressEvent[];
  /** Set once post-processing finished; until then the job is reported as running. */
  final: DownloadJobStatus | null;
}

export interface PostprocessHooks {
  needsPostprocess: (job: DownloadJobStatus) => boolean;
  run: (job: DownloadJobStatus, progress: ProgressEvent[]) => Promise<DownloadJobStatus>;
}

/**
 * Decide what to report for a job whose transfer succeeded but which still has a
 * post-processing step (e.g. making the MP4 playable). The first call starts the
 * step; until it finishes the job is reported as a non-terminal `running` job so
 * pollers keep going and the UI never shows "완료" early. Once it finished, the
 * final (succeeded/failed) job is returned on every later call.
 */
export function reportWithPostprocess(
  entries: Map<string, PostprocessEntry>,
  job: DownloadJobStatus,
  hooks: PostprocessHooks,
): DownloadJobStatus {
  if (job.status !== 'succeeded') return job;
  let entry = entries.get(job.job_id);
  if (!entry) {
    if (!hooks.needsPostprocess(job)) return job;
    const created: PostprocessEntry = { progress: [...job.progress], final: null };
    entry = created;
    entries.set(job.job_id, created);
    void hooks.run(job, created.progress)
      .catch((): DownloadJobStatus => ({ ...job, status: 'failed', error_code: 'video_reconstruction_failed', progress: created.progress }))
      .then((final) => {
        created.final = final;
      });
  }
  if (entry.final) return entry.final;
  return { ...job, status: 'running', result: null, progress: [...entry.progress] };
}
