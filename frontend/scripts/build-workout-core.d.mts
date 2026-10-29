// Tipos de `build-workout-core.mjs` (o tsconfig não usa `allowJs`).

export declare const ENTRY_POINT: string;
export declare const OUT_DIR: string;
export declare const BUNDLE_FILE: string;
export declare const GOLDEN_JSON: string;
export declare const GOLDEN_FILE: string;

export declare function goldenModuleSource(goldenJsonText?: string): string;

export type WorkoutCoreMeta = {
  commit: string;
  sha256: string;
  bytes: number;
  gzip_bytes: number;
  format: 'esm';
  target: 'es2020';
  golden_sha256: string;
  golden_cases: number;
};

export declare function buildWorkoutCore(options?: { write?: boolean }): Promise<{ code: string; goldenCode: string; meta: WorkoutCoreMeta }>;
