import { useState } from "react";

export default function Section({ title, helperText, defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section className="app-section">
      <button
        type="button"
        className="app-section-header"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="app-section-title">{title}</span>
        <span className="app-section-chevron">{open ? "▾" : "▸"}</span>
      </button>
      {helperText && <p className="app-section-helper">{helperText}</p>}
      {open && <div className="app-section-body">{children}</div>}
    </section>
  );
}
