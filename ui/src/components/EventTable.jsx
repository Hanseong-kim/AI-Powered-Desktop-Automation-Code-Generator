import React, { useEffect, useRef } from 'react';

const ACTION_COLORS = {
  click: 'blue',
  doubleClick: 'purple',
  rightClick: 'orange',
  type: 'green',
  scroll: 'grey',
};

export default function EventTable({ events }) {
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
              <th>Name</th>
              <th>AutomationId</th>
              <th>ClassName</th>
              <th>Window Title</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>
            {events.map((ev, i) => (
              <tr key={ev.index ?? i}>
                <td className="mono">{ev.index ?? i + 1}</td>
                <td>
                  <span className={`action-badge ${ACTION_COLORS[ev.action] ?? ''}`}>
                    {ev.action}
                  </span>
                </td>
                <td className="truncate">{ev.element?.name ?? ''}</td>
                <td className="mono truncate">{ev.element?.automationId ?? ''}</td>
                <td className="mono truncate">{ev.element?.className ?? ''}</td>
                <td className="truncate">{ev.element?.windowTitle ?? ''}</td>
                <td className="truncate">{ev.value ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div ref={bottomRef} />
      </div>
    </section>
  );
}
