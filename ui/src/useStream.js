import { useEffect } from 'react';

/**
 * Subscribes to the Express SSE stream at /api/stream.
 * Calls the provided handlers for each event type.
 * Reconnects automatically on error.
 *
 * onDisconnect — fires exactly once when the connection first drops.
 * onReconnect  — fires on es.onopen only after a prior disconnect (not on initial connect).
 */
export function useStream({ onSnapshot, onStatus, onCapture, onGeneration, onDisconnect, onReconnect }) {
  useEffect(() => {
    let es;
    let retryTimer;
    let lostConnection = false; // true after first onerror; reset on onopen

    function connect() {
      es = new EventSource('/api/stream');

      es.onopen = () => {
        if (lostConnection) {
          lostConnection = false;
          onReconnect?.();
        }
      };

      es.addEventListener('snapshot', (e) => {
        onSnapshot?.(JSON.parse(e.data));
      });
      es.addEventListener('status', (e) => {
        onStatus?.(JSON.parse(e.data));
      });
      es.addEventListener('capture', (e) => {
        onCapture?.(JSON.parse(e.data));
      });
      es.addEventListener('generation', (e) => {
        onGeneration?.(JSON.parse(e.data));
      });

      es.onerror = () => {
        es.close();
        if (!lostConnection) {
          lostConnection = true;
          onDisconnect?.();
        }
        retryTimer = setTimeout(connect, 3000);
      };
    }

    connect();
    return () => {
      clearTimeout(retryTimer);
      es?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
