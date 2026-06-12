import React from 'react';

export function Toast({ toasts, dismiss }) {
  if (!toasts.length) return null;
  return (
    <div className="toast-container">
      {toasts.map(({ id, type, message }) => (
        <div key={id} className={`toast ${type}`} role="alert">
          <span>{message}</span>
          <button onClick={() => dismiss(id)} aria-label="dismiss">×</button>
        </div>
      ))}
    </div>
  );
}
