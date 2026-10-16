import { useCallback, useEffect, useRef, useState } from 'react';
import { downloadCanvasFile, fetchPageFiles, isAuthenticationError } from '../lib/api';
import { itemKey } from '../lib/content';
import { friendlyError } from '../lib/errors';
import type { CourseContentItem, CourseSummary, PageFile } from '../lib/types';

export type FileTransfer = 'busy' | 'done' | 'error';

export interface PageAttachments {
  state: 'loading' | 'ready' | 'error';
  files: PageFile[];
  error: string | null;
  /** Per file id: being saved, saved just now, or failed. */
  transfers: Record<string, FileTransfer>;
}

interface Options {
  onAuthError: (message: string) => void;
  notify: (message: string, kind?: 'info' | 'success' | 'error') => void;
}

/**
 * The files linked from wiki pages ("1주차 강의자료"): a page row can be expanded to list them
 * (fetched on demand, once per page) and each file saved into the course folder.
 */
export function usePageAttachments(course: CourseSummary | null, outputDir: string, { onAuthError, notify }: Options) {
  const [pages, setPages] = useState<Record<string, PageAttachments>>({});
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const courseKey = course === null ? null : String(course.id);
  const courseKeyRef = useRef(courseKey);
  courseKeyRef.current = courseKey;
  const optionsRef = useRef({ onAuthError, notify });
  useEffect(() => {
    optionsRef.current = { onAuthError, notify };
  });

  // Another course (or folder) means other pages and other "saved" marks.
  useEffect(() => {
    setPages({});
    setExpanded(new Set());
  }, [courseKey, outputDir]);

  const patch = useCallback((key: string, change: (current: PageAttachments) => PageAttachments) => {
    setPages((current) => (current[key] ? { ...current, [key]: change(current[key]) } : current));
  }, []);

  const load = useCallback(async (item: CourseContentItem, key: string) => {
    if (!course || !item.page_ref) return;
    const requestedFor = String(course.id);
    setPages((current) => ({ ...current, [key]: { state: 'loading', files: [], error: null, transfers: {} } }));
    try {
      const response = await fetchPageFiles(course.id, item.page_ref, course.name, outputDir);
      if (courseKeyRef.current !== requestedFor) return;
      patch(key, (current) => ({ ...current, state: 'ready', files: response.files }));
    } catch (error) {
      if (courseKeyRef.current !== requestedFor) return;
      if (isAuthenticationError(error)) {
        optionsRef.current.onAuthError(friendlyError(error));
        return;
      }
      patch(key, (current) => ({ ...current, state: 'error', error: friendlyError(error, '첨부 파일을 불러오지 못했어요.') }));
    }
  }, [course, outputDir, patch]);

  const toggle = useCallback((item: CourseContentItem) => {
    const key = itemKey(item);
    if (key === null) return;
    const opening = !expanded.has(key);
    setExpanded((current) => {
      const next = new Set(current);
      if (opening) next.add(key);
      else next.delete(key);
      return next;
    });
    if (opening && (!pages[key] || pages[key].state === 'error')) void load(item, key);
  }, [expanded, load, pages]);

  const retry = useCallback((item: CourseContentItem) => {
    const key = itemKey(item);
    if (key !== null) void load(item, key);
  }, [load]);

  const save = useCallback(async (item: CourseContentItem, file: PageFile) => {
    const key = itemKey(item);
    if (!course || key === null) return;
    const requestedFor = String(course.id);
    const mark = (transfer: FileTransfer) => patch(key, (current) => ({ ...current, transfers: { ...current.transfers, [file.file_id]: transfer } }));
    mark('busy');
    try {
      const result = await downloadCanvasFile(course.id, file.file_id, course.name, outputDir);
      if (courseKeyRef.current !== requestedFor) return;
      mark('done');
      patch(key, (current) => ({
        ...current,
        files: current.files.map((candidate) => (candidate.file_id === file.file_id ? { ...candidate, downloaded_path: result.output_path } : candidate)),
      }));
      optionsRef.current.notify(result.skipped ? `${file.display_name}은(는) 이미 받아 두었어요.` : `${file.display_name}을(를) 받았어요.`, 'success');
    } catch (error) {
      if (courseKeyRef.current !== requestedFor) return;
      mark('error');
      if (isAuthenticationError(error)) optionsRef.current.onAuthError(friendlyError(error));
      else optionsRef.current.notify(friendlyError(error, '파일을 받지 못했어요.'), 'error');
    }
  }, [course, outputDir, patch]);

  return { pages, expanded, toggle, retry, save };
}

export type PageAttachmentsController = ReturnType<typeof usePageAttachments>;
