/**
 * Three views, one at a time: a centered handle input, a centered progress
 * screen while the pipeline runs, and the slide deck on its own once the
 * payload lands. Nothing else is on screen at any point -- this is the
 * presentation build, so the raw-payload dump and the always-visible form
 * that used to live here are gone.
 *
 * The interesting file is api.ts -- runWrapped() handles start, polling,
 * progress and errors.
 */
import { useState } from 'react';
import { runWrapped, type JobStatus, type WrappedPayload } from './api';
import SlideCarousel from './Carousel';

const PHASE_LABEL: Record<JobStatus, string> = {
  pending: 'Queued',
  ingesting: 'Reading GitHub',
  computing: 'Crunching stats',
  generating: 'Designing slides',
  ready: 'Done',
  error: 'Failed',
};

/** Progress order, so the loading screen can show what is behind and ahead. */
const PHASES: JobStatus[] = ['pending', 'ingesting', 'computing', 'generating'];

export default function App() {
  const [handle, setHandle] = useState('');
  const [refresh, setRefresh] = useState(false);
  const [phase, setPhase] = useState<JobStatus | null>(null);
  const [data, setData] = useState<WrappedPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const running = phase !== null && phase !== 'ready' && phase !== 'error';

  async function go(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setData(null);
    setPhase('pending');
    try {
      setData(await runWrapped(handle.trim(), setPhase, { refresh }));
      setPhase('ready');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase('error');
    }
  }

  function reset() {
    setData(null);
    setPhase(null);
    setError(null);
  }

  if (data) {
    return (
      <SlideCarousel
        data={{ slides: data.slides }}
        user={data.user}
        onExit={reset}
      />
    );
  }

  if (running) return <Loading handle={handle} phase={phase!} />;

  return (
    <main className="screen">
      <div className="landing">
        <h1 className="title">
          GitHub <span className="title-accent">Wrapped</span>
        </h1>
        <p className="tagline">Your year in code, in five slides.</p>

        <form onSubmit={go} className="handle-form">
          <div className="handle-row">
            <span className="at">@</span>
            <input
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              placeholder="github handle"
              autoFocus
              autoComplete="off"
              spellCheck={false}
              aria-label="GitHub handle"
            />
            <button type="submit" disabled={!handle.trim()}>
              Wrap it
            </button>
          </div>

          <label className="refresh">
            <input
              type="checkbox"
              checked={refresh}
              onChange={(e) => setRefresh(e.target.checked)}
            />
            Force re-run
          </label>
        </form>

        {error && <p className="error">{error}</p>}
      </div>
    </main>
  );
}

function Loading({ handle, phase }: { handle: string; phase: JobStatus }) {
  const at = PHASES.indexOf(phase);

  return (
    <main className="screen">
      <div className="loading">
        <div className="spinner" aria-hidden="true" />
        <p className="loading-phase">{PHASE_LABEL[phase]}</p>
        <p className="loading-handle">@{handle}</p>
        <ol className="steps" aria-label="pipeline progress">
          {PHASES.map((p, i) => (
            <li
              key={p}
              className={i < at ? 'step done' : i === at ? 'step now' : 'step'}
            />
          ))}
        </ol>
      </div>
    </main>
  );
}
