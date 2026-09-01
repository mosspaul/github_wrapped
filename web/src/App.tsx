/**
 * Deliberately minimal harness. It proves the pipeline works end to end and
 * gives whoever builds the real slide deck a working client to start from.
 *
 * The interesting file is api.ts -- runWrapped() already handles start,
 * polling, progress and errors. Replace this component freely.
 */
import { useState } from 'react';
import { runWrapped, type JobStatus, type WrappedPayload } from './api';
import SlideCarousel from './Carousel';

const PHASE_LABEL: Record<JobStatus, string> = {
  pending: 'Queued...',
  ingesting: 'Reading GitHub...',
  computing: 'Crunching stats...',
  generating: 'Designing slides...',
  ready: 'Done',
  error: 'Failed',
};

export default function App() {
  const [handle, setHandle] = useState('octocat');
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

  return (
    <main>
      <h1>GitHub Wrapped</h1>
      <p className="muted">
        Infrastructure smoke test. Enter a handle; this runs the full pipeline
        and dumps the raw payload.
      </p>

      <form onSubmit={go} style={{ margin: '1.5rem 0' }}>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <input
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            placeholder="github handle"
            disabled={running}
          />
          <button type="submit" disabled={running || !handle.trim()}>
            {running ? 'Working...' : 'Run'}
          </button>
        </div>

        <label
          className="muted"
          style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '0.6rem' }}
        >
          <input
            type="checkbox"
            checked={refresh}
            onChange={(e) => setRefresh(e.target.checked)}
            disabled={running}
          />
          Force re-run (ignore the cached deck; takes ~30s instead of ~2s)
        </label>
      </form>

      {phase && !error && <p>{PHASE_LABEL[phase]}</p>}
      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <h2>{data.user.displayName ?? data.user.handle}</h2>

          <SlideCarousel data={{slides: data.slides}}/>

          <h3>Raw payload</h3>
          <pre>{JSON.stringify(data, null, 2)}</pre>
        </>
      )}
    </main>
  );
}
