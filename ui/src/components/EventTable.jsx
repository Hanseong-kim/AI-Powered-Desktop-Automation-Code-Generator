import React, { useEffect, useRef } from 'react';

const ACTION_COLORS = {
  click: 'blue',
  doubleClick: 'purple',
  rightClick: 'orange',
  type: 'green',
  scroll: 'grey',
};

// Mirrors the T1/T2/T3 selector-quality axis (see
// note/project/code-generator/app-selector-coverage.md): agent.py already
// computes locatorStrategy per event (agent.py:1704-1720) using the same
// priority order codegen's wdioSelectorById falls back through, so this is
// a read of an existing field, not a new judgement.
const T1_STRATEGIES = new Set(['automationId']);
const T2_STRATEGIES = new Set(['name', 'className', 'xpath', 'anchorXPath']);

function selectorTier(ev) {
  const strategy = ev.element?.locatorStrategy || '';
  if (T1_STRATEGIES.has(strategy)) return { label: 'T1', color: 'green' };
  if (T2_STRATEGIES.has(strategy)) return { label: 'T2', color: 'yellow' };
  if (strategy === 'coordinate') return { label: 'T3', color: 'red' };
  // Older captures (or non-element actions like scroll) carry no
  // locatorStrategy at all -- fall back to the raw fields so the column
  // still means something instead of going blank.
  const el = ev.element || {};
  if (el.automationId) return { label: 'T1', color: 'green' };
  if (el.name || el.className) return { label: 'T2', color: 'yellow' };
  return { label: '?', color: 'grey' };
}

// A double-click always shows up in the raw capture as click, click,
// doubleClick (agent.py:4254-4257) — every physical press is kept
// individually so a genuine run of repeated single presses on the same spot
// (e.g. typing "9999" on a calculator) never gets silently merged. That's
// correct for the underlying data, but three rows for one user gesture is
// noisy here, so this groups them for DISPLAY only — deleting a merged row
// still deletes all three underlying events (see handleDeleteEvent).
const DOUBLE_CLICK_INTERVAL = 0.5; // seconds — mirrors agent.py's constant

function sameTarget(a, b) {
  const aId = a.element?.automationId, bId = b.element?.automationId;
  if (aId || bId) return aId === bId;
  return (a.element?.name || '') === (b.element?.name || '');
}

function groupEvents(events) {
  const groups = [];
  let i = 0;
  while (i < events.length) {
    const a = events[i], b = events[i + 1], c = events[i + 2];
    if (
      a && b && c &&
      a.action === 'click' && b.action === 'click' && c.action === 'doubleClick' &&
      typeof a.timestamp === 'number' && typeof c.timestamp === 'number' &&
      c.timestamp - a.timestamp <= DOUBLE_CLICK_INTERVAL &&
      sameTarget(a, b) && sameTarget(b, c)
    ) {
      groups.push({ display: c, arrayIndices: [i, i + 1, i + 2], mergedCount: 3 });
      i += 3;
    } else {
      groups.push({ display: a, arrayIndices: [i], mergedCount: 1 });
      i += 1;
    }
  }
  return groups;
}

export default function EventTable({ events, onDeleteEvent }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length]);

  return (
    <section className="event-table-section">
      <h2>Captured Events <span className="count">({events.length})</span></h2>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Action</th>
              <th title="T1 = stable AutomationId. T2 = Name/ClassName/anchor fallback. T3 = no usable selector, generates an explicit FAIL step.">Selector</th>
              <th>Name</th>
              <th>AutomationId</th>
              <th>ClassName</th>
              <th>Window Title</th>
              <th>Value</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {groupEvents(events).map(({ display: ev, arrayIndices, mergedCount }) => {
              const tier = selectorTier(ev);
              const i = arrayIndices[arrayIndices.length - 1];
              return (
              <tr key={ev.index ?? i} className="event-row">
                <td className="mono">{ev.index ?? i + 1}</td>
                <td>
                  <span className={`action-badge ${ACTION_COLORS[ev.action] ?? ''}`}>
                    {ev.action}
                  </span>
                  {mergedCount > 1 && (
                    <span className="badge grey" style={{ marginLeft: 4 }} title={`${mergedCount} raw events merged for display (click, click, doubleClick)`}>
                      ×{mergedCount}
                    </span>
                  )}
                </td>
                <td>
                  <span
                    className={`badge ${tier.color}`}
                    title={ev.element?.locatorStrategy
                      ? `locatorStrategy: ${ev.element.locatorStrategy}`
                      : 'no locatorStrategy on this event (older capture or non-element action)'}
                  >
                    {tier.label}
                  </span>
                  {ev.isPopup && (
                    <span className="badge yellow" style={{ marginLeft: 4 }} title={`popup window: ${ev.popupTitle || ''}`}>
                      popup
                    </span>
                  )}
                </td>
                <td className="truncate">{ev.element?.name ?? ''}</td>
                <td className="mono truncate">{ev.element?.automationId ?? ''}</td>
                <td className="mono truncate">{ev.element?.className ?? ''}</td>
                <td className="truncate">{ev.element?.windowTitle ?? ''}</td>
                <td className="truncate">{ev.value ?? ''}</td>
                <td className="delete-cell">
                  <button
                    className="delete-row-btn"
                    title={mergedCount > 1 ? `Delete all ${mergedCount} merged events` : 'Delete event'}
                    onClick={() => onDeleteEvent?.(arrayIndices)}
                    aria-label={`Delete event ${ev.index ?? i + 1}`}
                  >
                    ×
                  </button>
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
        <div ref={bottomRef} />
      </div>
    </section>
  );
}
