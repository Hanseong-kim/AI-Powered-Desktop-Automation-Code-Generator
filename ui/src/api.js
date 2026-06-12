const BASE = '/api';

const json = (res) => {
  if (!res.ok && res.status !== 400 && res.status !== 500) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
};

const post = (path, body) =>
  fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(json);

export const getStatus    = ()     => fetch(`${BASE}/status`).then(json);
export const startRecording = (body) => post('/start', body);
export const stopRecording  = ()     => post('/stop', {});
export const clearEvents    = ()     =>
  fetch(`${BASE}/events`, { method: 'DELETE' }).then(json);
export const generate = (body) => post('/generate', body);
