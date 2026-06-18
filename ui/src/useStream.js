import { useEffect } from 'react';

const HEARTBEAT_TIMEOUT_MS = 22000; // server sends every 10s; allow 2 misses + buffer

/**
 * Subscribes to the Express SSE stream at /api/stream.
 * Uses heartbeat-based timeout to detect server loss even through Vite proxy.
 *
 * onDisconnect — fires exactly once when connection is considered lost.
 * onReconnect  — fires on successful reconnect after a prior disconnect.
 */
export function useStream({ onSnapshot, onStatus, onCapture, onGeneration, onDisconnect, onReconnect }) {
  useEffect(() => {
    let es;
    let retryTimer;
    let heartbeatTimer;
    let lostConnection = false;

    function scheduleHeartbeatTimeout() {
      clearTimeout(heartbeatTimer);
      heartbeatTimer = setTimeout(() => {
        // No heartbeat received — treat as disconnect
        es?.close();
        if (!lostConnection) {
          lostConnection = true;
          onDisconnect?.();
        }
        retryTimer = setTimeout(connect, 3000);
      }, HEARTBEAT_TIMEOUT_MS);
    }

    function connect() {
      es = new EventSource('/api/stream');

      es.onopen = () => {
        scheduleHeartbeatTimeout();
        if (lostConnection) {
          lostConnection = false;
          onReconnect?.();
        }
      };

      es.addEventListener('heartbeat', () => {
        scheduleHeartbeatTimeout();
      });

      es.addEventListener('snapshot', (e) => {
        scheduleHeartbeatTimeout();
        onSnapshot?.(JSON.parse(e.data));
      });
      es.addEventListener('status', (e) => {
        scheduleHeartbeatTimeout();
        onStatus?.(JSON.parse(e.data));
      });
      es.addEventListener('capture', (e) => {
        scheduleHeartbeatTimeout();
        onCapture?.(JSON.parse(e.data));
      });
      es.addEventListener('generation', (e) => {
        scheduleHeartbeatTimeout();
        onGeneration?.(JSON.parse(e.data));
      });

      es.onerror = () => {
        clearTimeout(heartbeatTimer);
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
      clearTimeout(heartbeatTimer);
      es?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
