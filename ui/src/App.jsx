import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useStream } from './useStream';
import { getConfig, getStatus, startRecording, stopRecording, clearEvents, generate, deleteEvent } from './api';
import ControlPanel from './components/ControlPanel';
import EventTable from './components/EventTable';
import CodeViewer from './components/CodeViewer';
import { Toast } from './components/Toast';

const DEFAULT_FORM = {
  preset: 'Calculator',
  appName: 'Calculator',
  exePath: 'C:\\Windows\\System32\\calc.exe',
  platform: 'Windows',
  framework: 'wdio',
  apiKey: '',
};

let _toastId = 0;

export default function App() {
  const [form, setForm] = useState(DEFAULT_FORM);
  const [status, setStatus] = useState({ agentOnline: false, isAdmin: null, recording: false });
  const [events, setEvents] = useState([]);
  const [genState, setGenState] = useState({ generating: false, files: null, error: null });
  const [hasServerKey, setHasServerKey] = useState(false);
  const [toasts, setToasts] = useState([]);
  const dismissTimers = useRef({});

  function addToast(type, message) {
    const id = ++_toastId;
    setToasts((prev) => [...prev, { id, type, message }]);
    dismissTimers.current[id] = setTimeout(() => removeToast(id), 4000);
  }

  function removeToast(id) {
    clearTimeout(dismissTimers.current[id]);
    delete dismissTimers.current[id];
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  // Ask the server whether it has a .env key. If so, the user can leave the
  // key field blank — the server uses its own key (never sent to the browser).
  useEffect(() => {
    let alive = true;
    getConfig()
      .then(({ hasServerKey }) => { if (alive) setHasServerKey(!!hasServerKey); })
      .catch(() => { /* server offline — treat as no key */ });
    return () => { alive = false; };
  }, []);

  // Poll agent status every 3 s
  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const s = await getStatus();
        if (alive) setStatus((prev) => ({ ...prev, ...s }));
      } catch {
        if (alive) setStatus((prev) => ({ ...prev, agentOnline: false }));
      }
    }
    poll();
    const id = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // SSE live feed
  useStream({
    onSnapshot: useCallback(({ events: evs, recording }) => {
      setEvents(evs ?? []);
      setStatus((prev) => ({ ...prev, recording: recording ?? prev.recording }));
    }, []),
    onStatus: useCallback(({ recording }) => {
      setStatus((prev) => ({ ...prev, recording }));
    }, []),
    onCapture: useCallback((ev) => {
      setEvents((prev) => [...prev, ev]);
    }, []),
    onGeneration: useCallback(({ status: s, files, message }) => {
      if (s === 'started') {
        setGenState({ generating: true, files: null, error: null });
      } else if (s === 'success') {
        setGenState((prev) => ({ ...prev, generating: false }));
      } else if (s === 'error') {
        setGenState({ generating: false, files: null, error: message });
        addToast('error', `Generation failed: ${message}`);
      }
    }, []),
    onDisconnect: useCallback(() => {
      addToast('warn', 'Server connection lost — reconnecting...');
    }, []),
    onReconnect: useCallback(() => {
      addToast('info', 'Server connection restored');
    }, []),
  });

  function handleFormChange(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function handlePresetChange(preset, appName, exePath) {
    setForm((prev) => ({ ...prev, preset, appName, exePath }));
  }

  async function handleLaunch() {
    try {
      const res = await startRecording({
        appName: form.appName,
        exePath: form.exePath,
        platform: form.platform,
      });
      if (!res.ok) addToast('error', `Launch failed: ${res.message}`);
    } catch (e) {
      addToast('error', `Launch error: ${e.message}`);
    }
  }

  async function handleStop() {
    try {
      await stopRecording();
    } catch (e) {
      addToast('warn', `Stop error: ${e.message}`);
    }
  }

  async function handleDeleteEvent(arrayIndex) {
    try {
      await deleteEvent(arrayIndex);
      // Server broadcasts snapshot; onSnapshot will update state.
    } catch (e) {
      addToast('warn', `Delete failed: ${e.message}`);
    }
  }

  async function handleClear() {
    try {
      await clearEvents();
      setEvents([]);
      setGenState({ generating: false, files: null, error: null });
    } catch (e) {
      addToast('warn', `Clear error: ${e.message}`);
    }
  }

  async function handleGenerate() {
    setGenState({ generating: true, files: null, error: null });
    try {
      const res = await generate({
        apiKey: form.apiKey,
        appName: form.appName || undefined,
        platform: form.platform || undefined,
        framework: form.framework || 'wdio',
      });
      if (res.ok) {
        setGenState({ generating: false, files: res.files, error: null });
        if (res.savedPaths?.length) {
          const dest = res.framework === 'playwright'
            ? 'generated-playwright/'
            : res.framework === 'wdio'
            ? 'generated-wdio/'
            : 'test-runner/…/tests/';
          addToast('info', `Files saved to ${dest}`);
        }
      } else {
        // SSE 'generation:error' event already fires addToast; only update state here
        setGenState({ generating: false, files: null, error: res.message });
      }
    } catch (e) {
      setGenState((prev) => ({ ...prev, files: null, error: e.message }));
      addToast('error', e.message);
    } finally {
      setGenState((prev) => ({ ...prev, generating: false }));
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Desktop Automation Code Generator</h1>
      </header>
      <main>
        <ControlPanel
          form={form}
          onFormChange={handleFormChange}
          onPresetChange={handlePresetChange}
          status={status}
          onLaunch={handleLaunch}
          onStop={handleStop}
          onClear={handleClear}
          onGenerate={handleGenerate}
          generating={genState.generating}
          eventCount={events.length}
          hasServerKey={hasServerKey}
        />
        <EventTable events={events} onDeleteEvent={handleDeleteEvent} />
        <CodeViewer files={genState.files} error={genState.error} />
      </main>
      <Toast toasts={toasts} dismiss={removeToast} />
    </div>
  );
}
