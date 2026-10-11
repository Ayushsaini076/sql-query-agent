import type { KeyboardEvent, PointerEvent } from "react";

interface Props {
  className?: string;
  /** Which way dragging right moves the panel edge: "grow" for a left panel, "shrink" for a right one. */
  direction: "grow" | "shrink";
  label: string;
  min: number;
  /** Largest allowed width, measured when a drag or key press starts. */
  getMax: () => number;
  /** The panel being resized, measured for its current width. */
  getPanel: () => HTMLElement | null;
  onResize: (width: number) => void;
  onCommit: (width: number) => void;
  onReset: () => void;
}

const KEY_STEP = 16;

export function ResizeHandle({ className = "", direction, label, min, getMax, getPanel, onResize, onCommit, onReset }: Props) {
  const sign = direction === "grow" ? 1 : -1;
  const clamp = (w: number, max: number) => Math.round(Math.min(Math.max(w, min), Math.max(min, max)));

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    const panel = getPanel();
    if (!panel || e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget;
    const startX = e.clientX;
    const startWidth = panel.getBoundingClientRect().width;
    const max = getMax();
    let width = startWidth;

    handle.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    const move = (ev: globalThis.PointerEvent) => {
      width = clamp(startWidth + sign * (ev.clientX - startX), max);
      onResize(width);
    };
    const end = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      document.body.classList.remove("resizing");
      if (width !== startWidth) onCommit(width);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const delta = e.key === "ArrowRight" ? KEY_STEP : e.key === "ArrowLeft" ? -KEY_STEP : 0;
    const panel = getPanel();
    if (!delta || !panel) return;
    e.preventDefault();
    const width = clamp(panel.getBoundingClientRect().width + sign * delta, getMax());
    onResize(width);
    onCommit(width);
  };

  return (
    <div
      className={`resize-handle ${className}`.trim()}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      title={`${label} (double-click to reset)`}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={onReset}
    />
  );
}
