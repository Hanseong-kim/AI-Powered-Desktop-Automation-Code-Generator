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

export default function CodeViewer({ files, error, folder, runCommand }) {
  const [activeTab, setActiveTab] = useState(0);
  const [copied, setCopied] = useState(false);

  if (error) {
    return (
      <section className="code-viewer">
        <div className="gen-error">Generation failed: {error}</div>
      </section>
    );
  }

  if (!files || files.length === 0) return null;

  const active = files[activeTab];

  function copyRunCommand() {
    if (!runCommand) return;
    navigator.clipboard.writeText(runCommand).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <section className="code-viewer">
      {folder && (
        // 이미 generated-wdio/<folder>/에 저장돼 있고 바로 실행 가능하다는 걸
        // 화면에 지속적으로 보여준다 — 이 정보를 몰라 코드를 수동으로 복사해
        // 새 파일을 만들던 워크플로우(2026-07-16 리뷰 피드백)를 없애기 위함.
        <div className="saved-banner">
          <span>✅ Saved to <code>generated-wdio/{folder}/</code> ({files.length} files shown, helpers included)</span>
          {runCommand && (
            <span className="run-cmd">
              run: <code>{runCommand}</code>
              <button className="btn copy-btn" onClick={copyRunCommand}>
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </span>
          )}
        </div>
      )}
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
