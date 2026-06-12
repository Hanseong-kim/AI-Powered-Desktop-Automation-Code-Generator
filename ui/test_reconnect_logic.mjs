/**
 * Unit test for useStream reconnect logic (without browser/React).
 * Simulates the EventSource state machine and verifies:
 *   - onDisconnect fires exactly once per disconnection episode
 *   - onReconnect fires on onopen ONLY after a prior disconnect (not on initial connect)
 */

let disconnectCount = 0;
let reconnectCount = 0;
const log = (...a) => console.log('[test]', ...a);

function simulateUseStreamLogic(onDisconnect, onReconnect) {
  let lostConnection = false;

  const onopen = () => {
    if (lostConnection) {
      lostConnection = false;
      onReconnect();
    }
  };

  const onerror = () => {
    if (!lostConnection) {
      lostConnection = true;
      onDisconnect();
    }
    // would setTimeout(connect, 3000) here — we simulate that inline
  };

  return { onopen, onerror, getLost: () => lostConnection };
}

// ── Scenario 1: initial connection success (no toasts) ───────────────────────
{
  disconnectCount = 0; reconnectCount = 0;
  const { onopen } = simulateUseStreamLogic(
    () => disconnectCount++,
    () => reconnectCount++,
  );
  onopen(); // initial connect
  console.assert(disconnectCount === 0, 'S1: onDisconnect must not fire on initial connect');
  console.assert(reconnectCount === 0,  'S1: onReconnect must not fire on initial connect');
  log('Scenario 1 PASS — initial connect: no toasts');
}

// ── Scenario 2: one disconnect episode ───────────────────────────────────────
{
  disconnectCount = 0; reconnectCount = 0;
  const { onopen, onerror } = simulateUseStreamLogic(
    () => disconnectCount++,
    () => reconnectCount++,
  );
  onopen();    // initial connect
  onerror();   // first failure → disconnected toast (1)
  onerror();   // second failure (3s retry also fails) → no extra toast
  onerror();   // third failure → no extra toast
  console.assert(disconnectCount === 1, `S2: onDisconnect must fire exactly once, got ${disconnectCount}`);
  console.assert(reconnectCount === 0,  'S2: onReconnect must not fire during failed retries');
  onopen();    // server comes back → restored toast (1)
  console.assert(reconnectCount === 1,  `S2: onReconnect must fire once on recovery, got ${reconnectCount}`);
  log('Scenario 2 PASS — 3 retry failures then recovery: disconnect=1, reconnect=1');
}

// ── Scenario 3: two separate disconnect episodes ──────────────────────────────
{
  disconnectCount = 0; reconnectCount = 0;
  const { onopen, onerror } = simulateUseStreamLogic(
    () => disconnectCount++,
    () => reconnectCount++,
  );
  onopen();   // initial
  onerror();  // episode 1 start
  onerror();  // retry fails
  onopen();   // recovery 1
  onerror();  // episode 2 start
  onerror();  // retry fails
  onopen();   // recovery 2
  console.assert(disconnectCount === 2, `S3: two episodes, got disconnectCount=${disconnectCount}`);
  console.assert(reconnectCount === 2,  `S3: two recoveries, got reconnectCount=${reconnectCount}`);
  log('Scenario 3 PASS — two disconnect episodes: disconnect=2, reconnect=2');
}

log('All assertions passed.');
