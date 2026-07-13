import React from 'react';

const PLATFORMS = ['Windows', 'Android', 'iOS'];
const PRESETS = [
  { label: 'Calculator',       appName: 'Calculator',       exePath: 'Microsoft.WindowsCalculator_8wekyb3d8bbwe!App' },
  { label: 'Notepad',          appName: 'Notepad',          exePath: 'C:\\Windows\\System32\\notepad.exe' },
  // UWP app: launch by AUMID (Get-StartApps AppID), not a versioned WindowsApps
  // exe path. The agent + generated code detect the "!" and launch via
  // explorer shell:AppsFolder (avoids ACL/version/activation breakage).
  { label: 'Paint (UWP)',      appName: 'Paint',            exePath: 'Microsoft.Paint_8wekyb3d8bbwe!App' },
  { label: 'Registry Editor',  appName: 'RegistryEditor',   exePath: 'C:\\Windows\\regedit.exe' },
  // 2026-07-10 스테이크홀더 권장 native 앱 (비승격 + 표준 Win32 컨트롤)
  { label: 'PuTTY',            appName: 'PuTTY',            exePath: 'C:\\Program Files\\PuTTY\\putty.exe' },
  { label: 'FileZilla',        appName: 'FileZilla',        exePath: 'C:\\Program Files\\FileZilla FTP Client\\filezilla.exe' },
  { label: 'IDM',              appName: 'IDM',              exePath: 'C:\\Program Files (x86)\\Internet Download Manager\\IDMan.exe' },
  // NOTE: LOCALAPPDATA is not available in CRA bundles; path uses "user" as default username.
  // If your Windows profile is not "user", select "Custom..." and enter the correct path.
  { label: 'VSCode',           appName: 'VSCode',           exePath: 'C:\\Users\\user\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe' },
  { label: 'GitHub Desktop',  appName: 'GitHubDesktop',    exePath: 'C:\\Users\\user\\AppData\\Local\\GitHubDesktop\\GitHubDesktop.exe' },
  { label: 'Free Download Manager', appName: 'FreeDM',       exePath: 'C:\\Program Files\\Softdeluxe\\Free Download Manager\\fdm.exe' },
  { label: 'Claude Desktop',        appName: 'ClaudeDesktop', exePath: 'Claude_pzs8sxrjxfjjc!Claude' },
  { label: 'Custom...',        appName: '',                 exePath: '' },
];

export default function ControlPanel({
  form, onFormChange, onPresetChange,
  status,
  onLaunch, onStop, onClear, onGenerate,
  generating,
  eventCount,
}) {
  const { agentOnline, isAdmin, recording } = status;
  const isCustom = form.preset === 'Custom...';

  function handlePresetSelect(label) {
    const found = PRESETS.find((p) => p.label === label);
    onPresetChange(label, found?.appName ?? '', found?.exePath ?? '');
  }

  function field(label, key, type = 'text', placeholder = '', locked = false) {
    return (
      <div className="field">
        <label>{label}</label>
        <input
          type={type}
          value={form[key]}
          placeholder={placeholder}
          onChange={(e) => onFormChange(key, e.target.value)}
          disabled={recording || locked}
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
        <div className="field">
          <label>Target App</label>
          <select
            value={form.preset}
            onChange={(e) => handlePresetSelect(e.target.value)}
            disabled={recording}
          >
            {PRESETS.map(({ label }) => (
              <option key={label}>{label}</option>
            ))}
          </select>
        </div>

        {field('App Name', 'appName', 'text', 'Calculator', !isCustom)}
        {field('Exe Path', 'exePath', 'text', 'C:\\Windows\\System32\\calc.exe', !isCustom)}

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

      </div>

      <div className="buttons">
        <button
          className="btn green"
          onClick={onLaunch}
          disabled={recording || !form.appName || !form.exePath}
        >
          Launch
        </button>
        <button
          className="btn red"
          onClick={onStop}
          disabled={false}
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
          disabled={generating || eventCount === 0}
        >
          {generating ? 'Generating...' : 'Generate Code'}
        </button>
      </div>
    </section>
  );
}
