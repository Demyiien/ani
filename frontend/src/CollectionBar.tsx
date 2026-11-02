import { useEffect, useState } from 'react';
import { Database, Download, Globe, RefreshCw, X } from 'lucide-react';
import type { useCollectedData } from './useCollectedData';

export default function CollectionBar({
  demo,
  setDemo,
  data,
  placeName,
}: {
  demo: boolean;
  setDemo: (v: boolean) => void;
  data: ReturnType<typeof useCollectedData>;
  placeName: string;
}) {
  const [hiddenJobId, setHiddenJobId] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  const visibleJob = data.job && data.job.id !== hiddenJobId ? data.job : null;
  const hasToast = (!demo && Boolean(data.error)) || Boolean(data.jobError) || Boolean(visibleJob);

  useEffect(() => {
    setClosing(false);
  }, [data.job?.id, data.error, data.jobError]);

  // Automatically fade out and dismiss finished collection popups after 4 seconds (unless source errors need review).
  useEffect(() => {
    if (!visibleJob || ['queued', 'running'].includes(visibleJob.status) || data.jobError || (!demo && data.error)) {
      return;
    }
    const jobId = visibleJob.id;
    let removeTimer: ReturnType<typeof setTimeout>;
    const fadeTimer = setTimeout(() => {
      setClosing(true);
      removeTimer = setTimeout(() => {
        setHiddenJobId(jobId);
        data.dismissNotice();
        setClosing(false);
      }, 260);
    }, 4000);
    return () => {
      clearTimeout(fadeTimer);
      clearTimeout(removeTimer);
    };
  }, [visibleJob?.id, visibleJob?.status, data.jobError, data.error, demo, data.dismissNotice]);

  const handleManualClose = () => {
    setClosing(true);
    const currentId = data.job?.id ?? null;
    setTimeout(() => {
      if (currentId) setHiddenJobId(currentId);
      data.dismissNotice();
      setClosing(false);
    }, 220);
  };

  return (
    <section className="collection-bar" aria-label="Data collection">
      <div className="collection-capsule">
        <div className="data-mode">
          <Database size={14} />
          <label htmlFor="data-mode" className="sr-only">
            Data view
          </label>
          <select
            id="data-mode"
            aria-label="Data view"
            value={demo ? 'demo' : 'stored'}
            onChange={e => setDemo(e.target.value === 'demo')}
          >
            <option value="stored">Collected data</option>
            <option value="demo">Simulated demo</option>
          </select>
        </div>
        {!demo && (
          <>
            <span className="hud-divider" />
            <button
              className="hud-pill-btn"
              disabled={data.running || data.submitting}
              onClick={() => data.collect('local')}
              title={`Collect point weather & MODIS for ${placeName}`}
            >
              <Download size={13} />
              <span>{data.submitting ? 'Starting…' : `Collect ${placeName}`}</span>
            </button>
            <button
              className="hud-pill-btn accent"
              disabled={data.running || data.submitting}
              onClick={() => data.collect('national_feeds')}
              title="Refresh nationwide CHIRPS, IMERG, Himawari-9, SMAP, MODIS, PAGASA & PSA"
            >
              <Globe size={13} />
              <span>Sync satellites &amp; advisories</span>
            </button>
            <button aria-label="Refresh collected data" title="Refresh collected data" className="refresh-data" onClick={data.refresh}>
              <RefreshCw size={14} />
            </button>
          </>
        )}
      </div>

      {hasToast && (
        <div className="collection-toast" data-closing={closing ? '' : undefined}>
          <div className="collection-toast-body">
            {!demo && data.error && (
              <p role="alert" className="collection-error">
                {data.error}
              </p>
            )}
            {data.jobError && (
              <p role="alert" className="collection-error">
                {data.jobError}
              </p>
            )}
            {visibleJob && (
              <div className="job-status" role="status">
                <span>
                  Collection {visibleJob.status}: {visibleJob.completed}/{visibleJob.total} requests · {visibleJob.rows.toLocaleString()} saved
                  {visibleJob.failed > 0 ? ` · ${visibleJob.failed} failed` : ''}
                </span>
                {data.running && <button onClick={data.cancel}>Stop remaining</button>}
                {visibleJob.errors.length > 0 && (
                  <details>
                    <summary>Source errors</summary>
                    {visibleJob.errors.map((e, i) => (
                      <p key={i}>
                        {e.source}: {e.error}
                      </p>
                    ))}
                  </details>
                )}
              </div>
            )}
          </div>
          <button
            type="button"
            className="toast-close"
            aria-label="Dismiss collection notification"
            title="Dismiss"
            onClick={handleManualClose}
          >
            <X size={14} />
          </button>
        </div>
      )}
    </section>
  );
}
