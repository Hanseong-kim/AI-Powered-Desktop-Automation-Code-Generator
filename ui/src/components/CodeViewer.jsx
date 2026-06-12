import React, { useState } from 'react';

function download(filename, content) {
  const blob = new Blob([content], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function CodeViewer({ files, error }) {
  const [activeTab, setActiveTab] = useState(0);

  if (error) {
    return (
      <section className="code-viewer">
        <div className="gen-error">Generation failed: {error}</div>
      </section>
    );
  }

  if (!files || files.length === 0) return null;

  const active = files[activeTab];

  return (
    <section className="code-viewer">
      <div className="tab-bar">
        {files.map((f, i) => (
          <button
            key={f.filename}
            className={`tab ${i === activeTab ? 'active' : ''}`}
            onClick={() => setActiveTab(i)}
          >
            {f.filename}
          </button>
        ))}
        <button
          className="btn blue download-btn"
          onClick={() => download(active.filename, active.content)}
        >
          Download {active.filename}
        </button>
      </div>
      <pre className="code-block"><code>{active.content}</code></pre>
    </section>
  );
}
