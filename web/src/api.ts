/**
 * Typed client for the wrapped API. Mirrors shared/CONTRACTS.md.
 *
 * Written so the real slide UI can be built against it without touching fetch
 * plumbing -- if you are doing front-end work, start from runWrapped().
 */

const BASE = import.meta.env.VITE_API_BASE as string | undefined;

if (!BASE) {
  console.warn('VITE_API_BASE is not set -- copy web/.env.example to web/.env.local');
}

export type JobStatus =
  | 'pending'
  | 'ingesting'
  | 'computing'
  | 'generating'
  | 'ready'
  | 'error';

export interface StatusResponse {
  handle: string;
  status: JobStatus;
  error: string | null;
  updatedAt: string;
}

export interface WrappedUser {
  handle: string;
  displayName: string | null;
  profileImageUrl: string | null;
  bio: string | null;
  followers: number;
  publicRepos: number;
  accountCreatedAt: string | null;
}

export interface WrappedSlide {
  slideType: string;
  title: string;
  stats: unknown;
  html: string | null;
  generatedAt: string | null;
}

export interface WrappedPayload {
  user: WrappedUser;
  slides: WrappedSlide[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { error?: string }).error ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

/**
 * Start a run. `refresh` forces the pipeline to re-run even when this handle
 * is already `ready` -- without it the API short-circuits and returns the
 * stored deck untouched (see CONTRACTS.md). Use it when the data is stale or
 * the pipeline changed; leave it off for a repeat demo, which is the whole
 * point of the fast path.
 */
export const startWrapped = (handle: string, refresh = false) =>
  req<{ handle: string; status: JobStatus }>(
    `/wrapped/${handle}${refresh ? '?refresh=true' : ''}`,
    { method: 'POST' },
  );

export const getStatus = (handle: string) =>
  req<StatusResponse>(`/wrapped/${handle}/status`);

export const getWrapped = (handle: string) =>
  req<WrappedPayload>(`/wrapped/${handle}`);

/** Statuses that mean the pipeline has stopped moving. */
const TERMINAL: JobStatus[] = ['ready', 'error'];

/**
 * Kick off a run and poll until it finishes.
 *
 * `onProgress` fires on every poll so the UI can show which phase is running --
 * the pipeline takes 30s+ end to end, so showing nothing is not an option.
 *
 * timeoutMs must cover the whole pipeline, not just one phase -- it's a
 * client-side budget on top of three sequential Lambda timeouts (see
 * infra/02-app.yaml): ingest-github 300s + compute-stats 120s +
 * generate-slides 600s = 1020s worst case. A shorter client deadline used to
 * fire while the backend was still legitimately working and report a false
 * "timed out", even on runs that would have finished. Keep this above the
 * sum of those three, with room to spare.
 */
export async function runWrapped(
  handle: string,
  onProgress?: (status: JobStatus) => void,
  {
    intervalMs = 2000,
    timeoutMs = 1_200_000,
    refresh = false,
  }: { intervalMs?: number; timeoutMs?: number; refresh?: boolean } = {},
): Promise<WrappedPayload> {
  const started = await startWrapped(handle, refresh);

  // An already-`ready` handle comes back ready from the POST itself, with no
  // run started. Returning here keeps that path near-instant instead of
  // sleeping a full poll interval to re-learn what we were just told.
  if (started.status === 'ready') {
    onProgress?.('ready');
    return getWrapped(handle);
  }

  const deadline = Date.now() + timeoutMs;

  for (;;) {
    await new Promise((r) => setTimeout(r, intervalMs));

    const status = await getStatus(handle);
    onProgress?.(status.status);

    if (status.status === 'error') {
      throw new Error(status.error ?? 'the pipeline failed');
    }
    if (status.status === 'ready') {
      return getWrapped(handle);
    }
    if (Date.now() > deadline) {
      throw new Error(`timed out waiting for ${handle} (last status: ${status.status})`);
    }
  }
}
