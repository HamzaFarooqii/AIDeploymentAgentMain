// Vitest global test setup: adds the jest-dom matchers (toBeInTheDocument,
// toHaveClass, etc.) and their TypeScript augmentation of vitest's
// `expect`. Referenced from vitest.config.ts's `test.setupFiles`.
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// `@testing-library/react`'s own auto-cleanup normally hooks into a global
// `afterEach`, which only exists when vitest's `test.globals` option is on.
// This project intentionally imports test APIs explicitly instead of using
// globals, so we register the unmount/cleanup step ourselves; without it,
// each test's rendered DOM stays mounted for the rest of the file and later
// tests see duplicate elements from earlier renders.
afterEach(() => {
  cleanup();
});
