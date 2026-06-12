import React from 'react';

const PLATFORMS = ['Windows', 'Android', 'iOS'];
const FRAMEWORKS = [
  { value: 'appium', label: 'Appium Java (TestNG)' },
  { value: 'playwright', label: 'Playwright Python' },
];

export default function ControlPanel({
  form, onFormChange,
  status,
  onLaunch, onStop, onClear, onGenerate,
  generating,
}) {
  const { agentOnline, isAdmin, recording } = status;

  function field(label, key, type = 'text', placeholder = '') {
    return (
      <div className="field">
        <label>{label}</label>
        <input
          type={type}
          value={form[key]}
          placeholder={placeholder}
          onChange={(e) => onFormChange(key, e.target.value)}
          disabled={recording}
          autoComplete="off"
        />
      </div>
    );
  }

  return (
    <section className="control-panel">
      <div className="badges">
        <span className={`badge ${agentOnline ? 'green' : 'red'}`}>
          Agent {agentOnline ? 'ONLINE' : 'OFFLINE'}
        </span>
        {agentOnline && (
          <span className={`badge ${isAdmin ? 'green' : 'yellow'}`}>
            {isAdmin ? 'ADMIN' : 'NO ADMIN — element names may be empty'}
          </span>
        )}
        {recording && (
          <span className="badge live">
            <span className="live-dot" /> LIVE
          </span>
        )}
      </div>

      <div className="fields">
        {field('App Name', 'appName', 'text', 'Calculator')}
        {field('Exe Path', 'exePath', 'text', 'C:\\Windows\\System32\\calc.exe')}

        <div className="field">
          <label>Platform</label>
          <select
            value={form.platform}
            onChange={(e) => onFormChange('platform', e.target.value)}
            disabled={recording}
          >
            {PLATFORMS.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </div>

        <div className="field">
          <label>Output Framework</label>
          <select
            value={form.framework}
            onChange={(e) => onFormChange('framework', e.target.value)}
            disabled={recording || generating}
          >
            {FRAMEWORKS.map(({ value, label }) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>

        {field('Groq API Key', 'apiKey', 'password', 'gsk_...')}
      </div>

      <div className="buttons">
        <button
          className="btn green"
          onClick={onLaunch}
          disabled={recording || !form.exePath}
        >
          Launch
        </button>
        <button
          className="btn red"
          onClick={onStop}
          disabled={!recording}
        >
          Stop
        </button>
        <button
          className="btn"
          onClick={onClear}
          disabled={recording}
        >
          Clear Events
        </button>
        <button
          className="btn blue"
          onClick={onGenerate}
          disabled={recording || !form.apiKey || generating}
        >
          {generating ? 'Generating...' : 'Generate Code'}
        </button>
      </div>
    </section>
  );
}
