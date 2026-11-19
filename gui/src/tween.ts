/* R5 (2026-11-12): about two seconds of animation as the Overview's numbers rise and
   its charts draw, so a run feels live.

   How it works. The page writes the Overview as it always did, whole and final. Each
   thing that should move carries a key (`data-tw`) and what it is heading to:
     - a number:    data-v (the value) and data-f (its format);
     - a bar:       data-tws, the inline style measures that move ("height", "bottom,height");
     - a histogram: data-h, its heights in 0..1, on the <svg> that holds the two paths.
   `animate(root)` is called once after the Overview is written. For each key it compares
   the new target with the one it last saw: nothing changed, nothing moves; otherwise the
   mark is repainted from what it last showed and stepped to the target over DURATION_MS.
   When the time is up the mark is put back EXACTLY as the page wrote it, so the animation
   can add nothing and round nothing.

   The page is rewritten (innerHTML) on every new result, so a mark's element does not
   survive; its key does. That is why the state lives here, by key, and not on elements.

   Erasable TypeScript only (no enum, no namespace): test_gui_animation.py runs this file
   under node as it is. */

/** How long a mark takes to reach a new value. */
export const DURATION_MS = 2000;

/** Decelerating: quick at first, settling onto the value. */
export const ease = (t: number): number => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);

/** A number as the page writes it. "n<d>": grouped, d decimals (main.ts `num`);
 *  "p<d>": a share as a percentage (`pct`); "f<d>": d decimals, ungrouped. */
export function format(v: number, f: string): string {
  const d = Number(f.slice(1)) || 0;
  if (f[0] === "p") return (v * 100).toFixed(d) + "%";
  if (f[0] === "f") return v.toFixed(d);
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** A step histogram in a 300 x 100 box, from heights in 0..1: one path for the line, one
 *  for the fill. The card's own drawing and every frame of its animation come from here. */
export function stepPath(heights: number[]): { line: string; area: string } {
  const bw = 300 / Math.max(heights.length, 1);
  const Y = (v: number) => (100 - v * 90).toFixed(2);
  let d = `M0 ${Y(heights[0] ?? 0)}`;
  heights.forEach((v, i) => { if (i) d += `V${Y(v)}`; d += `H${((i + 1) * bw).toFixed(2)}`; });
  d += "V100";
  return { line: d, area: d + "H0Z" };
}

/** `values` stretched or squeezed to `n` places, each taking the value at its place: a
 *  histogram whose window moved keeps its shape while it moves. Nothing shown yet is flat. */
export function fit(values: number[], n: number): number[] {
  if (values.length === n) return values;
  if (!values.length) return new Array<number>(n).fill(0);
  return Array.from({ length: n },
    (_, i) => values[Math.min(values.length - 1, Math.floor((i + 0.5) * values.length / n))]);
}

const mix = (a: number[], b: number[], e: number) => b.map((v, i) => a[i] + (v - a[i]) * e);
const same = (a: number[], b: number[]) =>
  a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < 1e-9);

/** The clock, the frame and the reader's wish for less motion: the three things the
 *  engine asks of a browser, in one place so a test can stand in for them. */
export const host = {
  now: (): number => performance.now(),
  frame: (step: () => void): void => { requestAnimationFrame(step); },
  still: (): boolean => typeof matchMedia === "function"
    && matchMedia("(prefers-reduced-motion: reduce)").matches,
};

type Marked = HTMLElement | SVGElement;
/** One mark on the page now: where it is heading, how to draw a step, how to put it back. */
type Mark = { to: number[]; paint: (v: number[]) => void; settle: () => void };
type Run = { from: number[]; to: number[]; start: number };

/** By key: what each mark last showed or is heading to, and the ones still moving. */
const heading = new Map<string, number[]>();
const running = new Map<string, Run>();
let marks = new Map<string, Mark>();
let looping = false;

function read(el: Marked): Mark | null {
  const d = el.dataset;
  let mark: Mark | null = null;
  if (d.v !== undefined) {
    const f = d.f ?? "n0", written = el.textContent;
    mark = { to: [Number(d.v)],
             paint: v => { el.textContent = format(v[0], f); },
             settle: () => { el.textContent = written; } };
  } else if (d.tws) {
    const props = d.tws.split(",");
    const written = props.map(p => el.style.getPropertyValue(p));
    mark = { to: written.map(parseFloat),
             paint: v => props.forEach((p, i) => el.style.setProperty(p, v[i].toFixed(2) + "%")),
             settle: () => props.forEach((p, i) => el.style.setProperty(p, written[i])) };
  } else if (d.h !== undefined) {
    const area = el.querySelector(".hx-area"), line = el.querySelector(".hx-line");
    if (!area || !line) return null;
    const written = [area.getAttribute("d") ?? "", line.getAttribute("d") ?? ""];
    mark = { to: d.h ? d.h.split(",").map(Number) : [],
             paint: v => { const p = stepPath(v); area.setAttribute("d", p.area); line.setAttribute("d", p.line); },
             settle: () => { area.setAttribute("d", written[0]); line.setAttribute("d", written[1]); } };
  }
  // A value that is not a number is left as the page wrote it.
  return mark && mark.to.every(Number.isFinite) ? mark : null;
}

const at = (run: Run, now: number) =>
  mix(run.from, run.to, ease((now - run.start) / DURATION_MS));

function step(): void {
  const now = host.now();
  for (const [key, run] of Array.from(running)) {
    const mark = marks.get(key);
    if (now - run.start >= DURATION_MS) { running.delete(key); mark?.settle(); }
    else mark?.paint(at(run, now));
  }
  if (running.size) host.frame(step); else looping = false;
}

/** Call once after the Overview is written under `root`. */
export function animate(root: ParentNode): void {
  const now = host.now(), still = host.still();
  marks = new Map();
  for (const el of Array.from(root.querySelectorAll<Marked>("[data-tw]"))) {
    const key = el.dataset.tw ?? "", mark = read(el);
    if (!mark) continue;
    const run = running.get(key), was = heading.get(key);
    heading.set(key, mark.to);
    if (still) { running.delete(key); continue; }
    // Still moving to this same value: carry on, on the element that replaced the old one.
    if (run && same(run.to, mark.to)) { marks.set(key, mark); mark.paint(at(run, now)); continue; }
    // Nothing new: a redraw (a tab opened again, a status line) is not a result.
    if (!run && was && same(was, mark.to)) continue;
    const from = fit(run ? at(run, now) : was ?? [], mark.to.length);
    running.set(key, { from, to: mark.to, start: now });
    marks.set(key, mark);
    mark.paint(from);
  }
  if (running.size && !looping) { looping = true; host.frame(step); }
}

/** A new run: forget what the last one showed, so its numbers start from zero. */
export function resetTweens(): void {
  heading.clear();
  running.clear();
  marks = new Map();
}
