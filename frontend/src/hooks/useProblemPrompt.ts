import { useState, useSyncExternalStore } from 'react';
import { createProblemPromptController } from '../lib/problemPrompt.ts';
import type { ProblemKind, ProblemPromptState } from '../lib/problemPrompt.ts';

export interface ProblemPrompt {
  state: ProblemPromptState;
  report: (kind: ProblemKind) => boolean;
  close: () => void;
}

/** One controller for the whole app session (it lives in `App`, above every screen). */
export function useProblemPrompt(): ProblemPrompt {
  const [controller] = useState(createProblemPromptController);
  const state = useSyncExternalStore(controller.subscribe, controller.getState);
  return { state, report: controller.report, close: controller.close };
}
