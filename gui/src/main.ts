import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { getVersion } from "@tauri-apps/api/app";
import { getCurrentWindow } from "@tauri-apps/api/window";
/* Generated from figspec (scripts/gen_gui_themes.py), checked by test_gui_dark_theme.py
   and test_gui_light_theme.py: each theme's tokens, series, colour map (the report's
   colour -> this theme's) and chart chrome. The GUI never invents a colour. */
import DARK from "./dark_theme.json";
import LIGHT from "./light_theme.json";
import { animate, format, resetTweens, stepPath } from "./tween";

/* Loaded from a <script> tag rather than bundled: it is the same 1.42 MB file Core
   embeds in the report, kept byte-identical and checked by test_plotly_bundle_contract. */
declare const Plotly: any;

type Figure = {
  id: string;
  title: string;
  caption: string;
  /* The report's grouping; the GUI does not use it. */
  section: string;
  /* Which GUI panel this figure is on. Decided ONCE, in Core (reporter._FIGURE_PANEL),
     and carried in figures.json, so the GUI and Core cannot group differently. */
  panel: string;
  spec: { data: any[]; layout: any; config: any };
};

type Poll = {
  state: "idle" | "running" | "stopping" | "finished";
  changed: boolean;
  /* figures.json was rewritten since the last poll (every render, interims included). */
  figures_changed: boolean;
  age_s: number | null;
  panel: any | null;
  result: any | null;
  exit_code: number | null;
  stopping_for_s: number | null;
  /* Seconds since the app spawned Core; frozen at the exit. */
  elapsed_s: number | null;
  /* When Core exited, unix seconds. */
  finished_at: number | null;
  log_tail: string;
  note: string | null;
  /* run_status.json: inputs found, done and in progress, written from the run's first
     moment (GUI round 2, 2026-11-12); null until Core has written it. */
  status: any | null;
};

const $ = (id: string) => document.getElementById(id)!;
const val = (id: string) => ($(id) as HTMLInputElement).value;
/* What a missing value says. "Not measured" is the true claim, and the report uses this
   exact string for the same case, so the two surfaces agree. */
const NOT_MEASURED = "Not measured";

const num = (x: any, d = 0) => (typeof x === "number" ? x.toLocaleString(undefined,
  { minimumFractionDigits: d, maximumFractionDigits: d }) : NOT_MEASURED);
/** A number with a unit, or the missing statement -- never "undefined mV". */
const withUnit = (x: any, unit: string, d = 0) =>
  typeof x === "number" ? `${num(x, d)} ${unit}` : NOT_MEASURED;
/* Construct names come from the user's FASTA and reach the page through innerHTML. */
const esc = (x: unknown) => String(x).replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
const pct = (x: any, d = 1) =>
  typeof x === "number" ? (x * 100).toFixed(d) + "%" : NOT_MEASURED;
/* A number the Overview animates (R5; tween.ts). The span reads exactly as num() or pct()
   would write it ("n<d>", "p<d>"; "f<d>" is toFixed) and carries the value and the format,
   so the engine can count it from what the key last showed. Markup, so it is placed
   unescaped: give it numbers only, never a name from the user's files. */
const tw = (key: string, x: any, f = "n0") => typeof x === "number"
  ? `<span data-tw="${esc(key)}" data-v="${x}" data-f="${f}">${format(x, f)}</span>` : NOT_MEASURED;
const twUnit = (key: string, x: any, unit: string, d = 0) =>
  typeof x === "number" ? `${tw(key, x, "n" + d)} ${unit}` : NOT_MEASURED;
const dig = (o: any, path: string) =>
  path.split(".").reduce((v, k) => (v == null ? v : v[k]), o);
const base = (p: string) => p.replace(/\/+$/, "").split("/").pop() || p;
/* A metric entry's values, or null unless Core measured it (`status`, qc_integrator). */
const measured = (m: any) => (m && m.status === "measured" ? m : null);

/* A stat's value, sized down when it is the missing statement rather than a number. */
const big = (v: string, label = "") => {
  const small = label ? `<small>${label}</small>` : "";
  return v === NOT_MEASURED ? `<b class="na">${v}${small}</b>` : `<b>${v}${small}</b>`;
};

// =====================================================================================
// Themes. Dark is the default; Light (2026-11-12: for manuscript figures and poster
// screenshots) is chosen in Settings > Appearance and kept in this computer's storage.
// Core's specs carry the report's light colours; each is swapped through the theme's
// map (dark: light -> dark; light: the grid only), and the theme's chrome is laid over
// the layout, before plotly draws. The patterns below are the ones test_gui_dark_theme.py
// checks the map against, so a colour this finds is a colour the map covers.
// =====================================================================================
type ThemeName = "dark" | "light";
const THEME_KEY = "mrnaspect.theme";
const THEMES: Record<ThemeName, any> = { dark: DARK, light: LIGHT };
/* index.html's first script set the page from the same key before the first paint. */
let themeName: ThemeName = (() => {
  try { return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; }
  catch { return "dark"; }
})();
const theme = () => THEMES[themeName];

const COLOUR_RE = /#[0-9a-fA-F]{6}(?![0-9a-fA-F])/g;
const RGBA_RE = /rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)/g;

const hexOf = (r: number, g: number, b: number) =>
  "#" + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join("");

function themeColour(s: string): string {
  const cmap: Record<string, string> = theme().colors;
  if (s.trim().toLowerCase() === "white") return cmap["white"] ?? s;
  return s
    .replace(COLOUR_RE, m => cmap[m.toLowerCase()] ?? m)
    // An rgba() fill is mapped by its hex and keeps its alpha.
    .replace(RGBA_RE, (m, r, g, b, a) => {
      const d = cmap[hexOf(+r, +g, +b)];
      if (!d) return m;
      const n = parseInt(d.slice(1), 16);
      return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
    });
}

function themeWalk(node: any): any {
  if (typeof node === "string") return themeColour(node);
  if (Array.isArray(node)) return node.map(themeWalk);
  if (node && typeof node === "object") {
    const out: any = {};
    for (const k of Object.keys(node)) out[k] = themeWalk(node[k]);
    return out;
  }
  return node;
}

/** `over` laid onto `base`, recursively; a leaf in `over` wins. */
function merge(base: any, over: any): any {
  if (!over || typeof over !== "object" || Array.isArray(over)) return over;
  const out: any = { ...(base && typeof base === "object" && !Array.isArray(base) ? base : {}) };
  for (const k of Object.keys(over)) out[k] = merge(out[k], over[k]);
  return out;
}

/** A spec ready for this theme's panel: colours swapped, the theme's chrome over each axis. */
function themeSpec(spec: Figure["spec"]): Figure["spec"] {
  const s = themeWalk(spec);
  const T = theme();
  const L = T.layout;
  let lay = merge(s.layout ?? {}, {
    paper_bgcolor: L.paper_bgcolor, plot_bgcolor: L.plot_bgcolor, font: L.font,
    legend: { ...L.legend, bgcolor: "rgba(0,0,0,0)" }, hoverlabel: L.hoverlabel,
  });
  if (lay.title && typeof lay.title === "object") lay.title = merge(lay.title, L.title);
  for (const k of Object.keys(lay)) {
    if (!/^[xy]axis\d*$/.test(k)) continue;
    const ax = { ...lay[k] };
    if (typeof ax.title === "string") ax.title = { text: ax.title };
    lay[k] = merge(ax, L.axis);
    if (ax.rangeslider) lay[k].rangeslider = merge(ax.rangeslider, L.rangeslider);
  }
  if (Array.isArray(lay.updatemenus)) {
    // activecolor too: Plotly's default is near-white, so the active button's label (the
    // light ink) vanished into it.
    lay.updatemenus = lay.updatemenus.map((m: any) => ({
      ...m, bgcolor: T.tokens["surface-raised"], bordercolor: T.tokens["rule"],
      activecolor: T.tokens["rule"],
      font: { ...(m.font ?? {}), color: T.tokens["ink-primary"] },
    }));
  }
  /* The legend goes above the plot, right-aligned, over figspec's (right of the plot since
     2026-11-10). Under it (figspec's y = -0.18 until then) it sat on the x axis and the
     range slider once the chart was short. The title is drawn by the page (figureCard),
     so the top margin only has to hold the legend, or a control of Core's that stays
     visible. */
  lay.legend = { ...lay.legend, orientation: "h", x: 1, xanchor: "right", y: 1.02, yanchor: "bottom" };
  const visibleMenu = (lay.updatemenus ?? []).some((m: any) => m.visible !== false);
  lay.margin = { ...(lay.margin ?? {}), t: visibleMenu ? 64 : 28 };
  return { ...s, layout: onLadder(lay), config: { ...(s.config ?? {}), displaylogo: false, responsive: true } };
}

/** Switch the theme: the page's tokens (index.html's :root[data-theme] block), the title
 *  bar, the switch, and every chart, redrawn in the new theme's colours. */
function applyTheme(t: ThemeName, save = true) {
  themeName = t;
  if (t === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  if (save) {
    try { localStorage.setItem(THEME_KEY, t); } catch { /* kept for this session only */ }
  }
  // The title bar is the system's (core:window:allow-set-theme); outside the app, or
  // refused, the page switches all the same.
  try { void getCurrentWindow().setTheme(t).catch(() => {}); } catch { /* not in Tauri */ }
  for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>("[data-theme-choice]")))
    b.setAttribute("aria-checked", String(b.dataset.themeChoice === t));
  if (page === "run") renderCurrentPanel();
}
for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>("[data-theme-choice]")))
  b.addEventListener("click", () => applyTheme(b.dataset.themeChoice as ThemeName));

/** A step of the GUI's type ladder in px, read from the page's own :root (a checked copy
 *  of figspec.GUI_TYPE_PX), so a chart's text sits on the steps the page's does. */
function typePx(step: "micro" | "small"): number {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(`--type-${step}`));
  return Number.isFinite(v) ? v : (step === "micro" ? 13 : 15);
}

/** The chart's text on the GUI's ladder (2026-11-12: larger): ticks and annotations
 *  at micro, everything else (legend, axis titles, hover) at small. Core's specs carry
 *  the report's sizes, a step smaller, and the report keeps them. */
function onLadder(lay: any): any {
  const micro = typePx("micro"), small = typePx("small");
  const out = { ...lay, font: { ...(lay.font ?? {}), size: small } };
  out.legend = { ...(lay.legend ?? {}), font: { ...(lay.legend?.font ?? {}), size: small } };
  out.hoverlabel = { ...(lay.hoverlabel ?? {}), font: { ...(lay.hoverlabel?.font ?? {}), size: small } };
  if (Array.isArray(lay.annotations))
    out.annotations = lay.annotations.map((a: any) => ({ ...a, font: { ...(a.font ?? {}), size: micro } }));
  if (Array.isArray(lay.updatemenus))
    out.updatemenus = lay.updatemenus.map((m: any) => ({ ...m, font: { ...(m.font ?? {}), size: small } }));
  for (const k of Object.keys(out)) {
    if (!/^[xy]axis\d*$/.test(k)) continue;
    const ax = out[k];
    const title = typeof ax.title === "string" ? { text: ax.title } : (ax.title ?? {});
    out[k] = { ...ax, tickfont: { ...(ax.tickfont ?? {}), size: micro },
               title: { ...title, font: { ...(title.font ?? {}), size: small } } };
  }
  return out;
}

// =====================================================================================
// The shell: top bar, left menu, one page at a time (no router library).
// =====================================================================================
type Page = "start" | "run" | "log" | "help" | "device" | "display" | "about";
const SETTINGS_PAGES: Page[] = ["device", "display", "about"];
let page: Page = "start";

function showPage(p: Page) {
  page = p;
  for (const el of Array.from(document.querySelectorAll<HTMLElement>("main > .page")))
    el.hidden = el.id !== `page-${p}`;
  const inSettings = SETTINGS_PAGES.includes(p);
  $("mainnav").hidden = inSettings;
  $("settingsnav").hidden = !inSettings;
  $("pagetitle").textContent = $(`page-${p}`).dataset.title ?? "";
  for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>(".nav button[data-page]"))) {
    const target = b.dataset.page;
    const here = target === p || (target === "device" && b.dataset.opens === "settings" && inSettings);
    if (here) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  if (p === "run") renderCurrentPanel();
  if (p === "about") void renderAbout();
}

for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>(".nav button[data-page]"))) {
  b.addEventListener("click", () => {
    const target = b.dataset.page!;
    showPage(target === "back" ? (lastPoll && isActive(lastPoll) ? "run" : "start") : target as Page);
  });
}
$("menutoggle").addEventListener("click", () => $("app").classList.toggle("collapsed"));
/* Help opens its own page (2026-11-12: it opened About); About stays in Settings. */
$("help").addEventListener("click", () => showPage("help"));
$("toabout").addEventListener("click", () => showPage("about"));

// =====================================================================================
// Start: three cards, then one choice per screen (2026-11-12: the three-step form
// gave a first-time user too much at once). A card fixes Core's `mode` and the live flag,
// so neither is a field.
// =====================================================================================
type Kind = "live" | "pod5" | "bam";
type StepId = "input" | "reference" | "arm" | "model" | "sample" | "output" | "review";
let kind: Kind = "bam";
let step: StepId = "input";
const STEP_NAMES: Record<StepId, string> = {
  input: "Input", reference: "Reference", arm: "Modification", model: "Model",
  sample: "Sample", output: "Save to", review: "Review",
};
/* What a live run follows: MinKNOW's POD5 files, basecalled here, or the BAM files
   MinKNOW basecalls itself (bam_pass/ and bam_fail/ of the run). Chosen on the live
   card's input screen; the other two cards do not use it. */
let liveSource: "pod5" | "bam" = "pod5";
/* Whether a card reads BAM files: the BAM card, and a live run that follows them. Such a
   run basecalls nothing, so it has no model to choose and needs no device. */
const readsBam = (k: Kind) => k === "bam" || (k === "live" && liveSource === "bam");
/** The screens a card asks, in order: a run of BAM files has no model to choose. */
const stepsOf = (k: Kind): StepId[] => readsBam(k)
  ? ["input", "reference", "arm", "sample", "output", "review"]
  : ["input", "reference", "arm", "model", "sample", "output", "review"];
const KIND_TEXT: Record<Kind, { title: string; input: string; hint: string }> = {
  live: { title: "Live run", input: "MinKNOW run",
          hint: "any folder of the run: its run folder, its pod5/ or bam_pass/, its sample or experiment" },
  pod5: { title: "POD5 files", input: "POD5 folder",
          hint: "the folder holding the POD5 files, or the run folder above it" },
  bam:  { title: "BAM files", input: "BAM folder",
          hint: "BAMs of basecalled reads, aligned or not; mRNAspect aligns them" },
};
/* Core's mode for a card. Live is the real-time driver, in the mode of what it follows. */
const modeOf = (k: Kind) => (readsBam(k) ? "bam" : "pod5");
const isRealtime = () => kind === "live";

for (const card of Array.from(document.querySelectorAll<HTMLButtonElement>(".kind"))) {
  card.addEventListener("click", () => openFlow(card.dataset.kind as Kind));
}

function openFlow(k: Kind) {
  kind = k;
  const t = KIND_TEXT[k];
  $("flowtitle").textContent = t.title;
  $("inputlabel").textContent = t.input;
  AUTHORED["input"] = t.hint;
  ($("input") as HTMLInputElement).placeholder = t.hint;
  ($("pod5only") as HTMLElement).hidden = readsBam(k);
  ($("rthint") as HTMLElement).hidden = k !== "live";
  ($("livesource") as HTMLElement).hidden = k !== "live";
  /* A POD5 card needs the Device settings; BAM needs none, so it is never blocked. */
  $("devicebanner").hidden = readsBam(k) || machineIsSet();
  $("kindpick").hidden = true;
  $("flow").hidden = false;
  inputInfo = null;
  void refreshConfig().then(() => describeInput());
  void checkRunDir();
  setStep("input");
}

/** What a live run follows. The screens change with it (a BAM run has no Model screen),
 *  and Core is asked again about the folder, since it now counts other files there. */
function setLiveSource(m: "pod5" | "bam") {
  liveSource = m;
  for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>("[data-livesource]")))
    b.setAttribute("aria-checked", String(b.dataset.livesource === m));
  ($("pod5only") as HTMLElement).hidden = readsBam(kind);
  $("devicebanner").hidden = readsBam(kind) || machineIsSet();
  startedAs = null;
  setStep(step);
  void describeInput();
}
for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>("[data-livesource]")))
  b.addEventListener("click", () => setLiveSource(b.dataset.livesource as "pod5" | "bam"));

/** Show one screen; the step bar names this card's screens, the earlier ones as links. */
function setStep(s: StepId) {
  step = s;
  const order = stepsOf(kind);
  for (const el of Array.from($("setupfields").querySelectorAll<HTMLElement>("[data-step]")))
    el.hidden = el.dataset.step !== s;
  const at = order.indexOf(s);
  $("steps").innerHTML = order.map((id, i) => {
    const name = `${i + 1}. ${STEP_NAMES[id]}`;
    return `<li data-step="${id}"${id === s ? ' aria-current="step"' : ""}>${i < at
      ? `<button type="button" class="linkbtn" data-goto="${id}">${name}</button>` : name}</li>`;
  }).join("");
  for (const b of Array.from($("steps").querySelectorAll<HTMLButtonElement>("[data-goto]")))
    b.addEventListener("click", () => setStep(b.dataset.goto as StepId));
  $("next").hidden = s === "review";
  $("go").hidden = s !== "review";
  syncFlow();
}

/* Typed, else the config's: the same resolution the placeholders show. */
const eff = (id: string, key: string) =>
  val(id).trim() || String(dig(resolvedCfg, key) ?? "");

/* What the operator declares about the sample, as [field, config key, label]: the fields
   the report prints, with its names and in its order (reporter._DECLARED_FIELDS). Each
   goes to Core as its own flag, only when typed (start_qc). */
const SAMPLE: [string, string, string][] = [
  ["manufacturer", "sample.manufacturer",     "Manufacturer"],
  ["product",      "sample.product",          "Product"],
  ["batchid",      "sample.batch_id",         "Batch / lot ID"],
  ["sampleid",     "sample.sample_id",        "Sample ID"],
  ["mfgdate",      "sample.manufacture_date", "Manufacture date"],
  ["operator",     "sample.operator",         "Operator"],
];

/** What blocks Continue on this screen. Reference has no shipped default. The
 *  input is done when Core has named the folder it reads (describe_input) and, for a
 *  POD5 or BAM run, found inputs there; when Core cannot be asked, a named folder is
 *  enough, as before. The input folder may come from the config. */
function missingOnStep(s: StepId): string[] {
  if (s === "input") return inputReady() ? [] : ["input"];
  if (s === "reference") return refMode === "paste"
    ? (pastedOk() ? [] : ["reftext"]) : (eff("ref", "reference") ? [] : ["ref"]);
  /* The shipped config names no output folder, and Start refuses a run without one
     (Chromium check, 2026-11-12: the review said "not set" and Start would have failed). */
  if (s === "output") {
    if (!eff("output", "output_dir")) return ["output"];
    /* The run there would refuse this Start (another input or reference), or the name is
       not one folder name: the line under Run name says why, and Start stays off
       (2026-11-23). */
    return runDirState?.refusal || (runDirState?.error && runDirState.code !== "no_output_dir")
      ? ["runname"] : [];
  }
  return [];
}

function inputReady(): boolean {
  if (!eff("input", "input_dir")) return false;
  // Asking, or the field was edited since the answer: wait, so Continue can never pass
  // on the previous folder's answer.
  if (inputInfo?.pending) return false;
  if (inputInfo === null || inputInfo.failed) return true;
  return !!inputInfo.use && (kind === "live" || inputInfo.inputs > 0);
}

function syncFlow() {
  const miss = missingOnStep(step);
  $("missing").hidden = !miss.length;
  $("missing").textContent = miss.includes("runname")
    ? "* Give another run name" : "* Missing required values";
  ($("next") as HTMLButtonElement).disabled = miss.length > 0;
  const active = lastPoll ? isActive(lastPoll) : false;
  ($("go") as HTMLButtonElement).disabled = active
    || stepsOf(kind).some(s => missingOnStep(s).length > 0);
  renderReview();
}

$("next").addEventListener("click", () => {
  const order = stepsOf(kind), i = order.indexOf(step);
  if (i < order.length - 1) setStep(order[i + 1]);
});
$("back").addEventListener("click", () => {
  const order = stepsOf(kind), i = order.indexOf(step);
  if (i > 0) { setStep(order[i - 1]); return; }
  $("flow").hidden = true;
  $("kindpick").hidden = false;
});
$("todevice").addEventListener("click", () => showPage("device"));

// --- the input: any level of a MinKNOW run (2026-11-12) ---------------------------
/* Core's answer for the folder named: the folder it reads (`use`), how many inputs it
   holds now, the run it belongs to, or the runs below it. null = not asked yet;
   {pending} = asking, or the field changed since; {failed} = Core could not be asked. */
let inputInfo: any = null;
let inputGen = 0;

/** Ask Core which folder a run of this card reads from `path` (blank: MinKNOW's data
 *  folder). The folder it reads replaces the one picked, so Start sends that one. */
async function describeInput(path = eff("input", "input_dir"), find = false) {
  const gen = ++inputGen;
  if (!path && !find) { inputInfo = null; renderInputInfo(); syncFlow(); return; }
  inputInfo = { pending: true };
  syncFlow();
  $("inputinfo").innerHTML = `<p class="dim">Looking…</p>`;
  try {
    const d = await invoke<any>("describe_input", {
      config: val("config"), path: find ? "" : path,
      mode: modeOf(kind), live: isRealtime(),
    });
    if (gen !== inputGen) return;
    inputInfo = d;
    if (d.use && !find && d.use !== val("input").trim())
      ($("input") as HTMLInputElement).value = d.use;
  } catch (e) {
    if (gen !== inputGen) return;
    inputInfo = { failed: String(e) };
  }
  renderInputInfo();
  syncFlow();
  /* The input Start would send may have moved (d.use): ask again whether the run of this
     name would take it. */
  void checkRunDir();
}

/** A MinKNOW run by the names typed into MinKNOW, its experiment and sample, when its
 *  folder is named as MinKNOW names one (2026-11-13: the run folder's own name is
 *  a timestamp two levels below them); else the folder's name. */
const runTitle = (r: any) => (r.experiment && r.sample ? `${r.experiment} / ${r.sample}` : r.name);

function renderInputInfo() {
  const d = inputInfo, el = $("inputinfo");
  if (!d || d.pending) { el.innerHTML = ""; return; }
  if (d.failed) { el.innerHTML = `<p class="dim err">${esc(d.failed)}</p>`; return; }
  const what = readsBam(kind) ? "BAM" : "POD5";
  if (d.use) {
    const n: number = d.inputs ?? 0;
    const ok = kind === "live" || n > 0;
    const files = `${num(n)} ${what} file${n === 1 ? "" : "s"}${kind === "live" ? " so far" : ""}`;
    const r = d.run;
    const facts = r ? [`Run ${runTitle(r)}`, r.flow_cell ? `flow cell ${r.flow_cell}` : "",
                       r.started ? `started ${r.started}` : "",
                       r.finished ? "finished" : "still running"].filter(Boolean) : [];
    const moved = d.moved
      ? `<p class="dim">You picked ${esc(d.picked)}; mRNAspect reads the ${d.moved === "up"
          ? "run folder above it" : "folder below it"}.</p>` : "";
    /* A BAM run of MinKNOW's folders. Whether the reads MinKNOW failed (bam_fail/) are in
       is the operator's choice, made here, before the run. The box shows which of the two
       folders is picked: the run folder reads both, bam_pass/ the passed reads only.
       Changing it picks the other folder and asks Core again, so the count, the note and
       what Start sends all come from Core. No box where Core names no second folder.
       A live run of MinKNOW's BAM files has the same box: the run folder follows both
       folders, bam_pass/ the passed reads only. Core offers it before any failed read
       is written, since a live run is set up before its files exist. */
    const nFail: number = (readsBam(kind) && d.bam) ? d.bam.fail.inputs : 0;
    const withFail = (readsBam(kind) && d.bam && d.bam.switch)
      ? `<label class="choice"><input type="checkbox" id="withfail"${d.bam.fail_included ? " checked" : ""}` +
        `/><span><b>Include the failed reads</b><small>${num(nFail)} BAM file${nFail === 1 ? "" : "s"} ` +
        `in bam_fail/${kind === "live" ? " so far" : ""}, the reads MinKNOW failed on their qscore` +
        `</small></span></label>` : "";
    const note = d.note ? `<p class="dim">${esc(d.note)}</p>` : "";
    el.innerHTML = `<div class="found${ok ? "" : " none"}"><b>${ok ? "✓" : "⚠"} ${esc(files)}</b>` +
      `<span class="dim">${esc(d.use)}</span></div>` +
      (facts.length ? `<div class="runfacts">${facts.map(f => `<span>${esc(f)}</span>`).join("")}</div>` : "") +
      moved + withFail + note;
    const box = el.querySelector<HTMLInputElement>("#withfail");
    if (box) box.addEventListener("change", () => {
      ($("input") as HTMLInputElement).value = d.bam.switch;
      startedAs = null;
      void describeInput(d.bam.switch);
    });
    return;
  }
  const rows = (d.runs ?? []).map((run: any) =>
    `<button type="button" class="runrow" data-use="${esc(run.use)}"><b title="${esc(run.path)}">${esc(runTitle(run))}</b>` +
    `<span>${esc(run.started ?? "")}</span><span>${esc(run.flow_cell ?? "")}</span>` +
    `<span>${num(run.inputs)} ${what}</span><span>${run.finished ? "finished" : "running"}</span></button>`).join("");
  el.innerHTML = (d.note ? `<p class="dim${rows ? "" : " err"}">${esc(d.note)}</p>` : "") +
    (rows ? `<div class="runlist">${rows}</div>` : "");
  for (const b of Array.from(el.querySelectorAll<HTMLButtonElement>(".runrow"))) {
    b.addEventListener("click", () => {
      ($("input") as HTMLInputElement).value = b.dataset.use!;
      startedAs = null;
      void describeInput(b.dataset.use!);
    });
  }
}

$("input").addEventListener("change", () => void describeInput());
/* Typing makes the last answer stale until the field is left (change asks again). */
$("input").addEventListener("input", () => {
  ++inputGen;
  inputInfo = { pending: true };
  renderInputInfo();
  syncFlow();
});
$("findruns").addEventListener("click", () => void describeInput("", true));

// --- the reference: a FASTA file, or the sequence pasted (2026-11-13) ------------
/* Core reads files, so a pasted reference becomes a FASTA file the shell writes beside
   the run's logs (start_qc). The rule for a sequence is Core's (runtime/reference_check
   _BASES, held equal by test_gui_mac_review3.py): A, C, G, T or U, any case. Whitespace
   between bases is dropped; anything else is refused here, with its record, rather than
   at Start. U is written as T, as a DNA reference is (Core reads either). */
const FASTA_BASES = /^[ACGTUacgtu]*$/;
let refMode: "file" | "paste" = "file";

type Pasted = { fasta: string; records: [string, number][] } | { error: string } | null;

/** The pasted text as FASTA, or why it is not one; null when nothing is pasted. A bare
 *  sequence (no '>' line) takes the Name field as its name. */
function pastedFasta(): Pasted {
  const text = val("reftext");
  if (!text.trim()) return null;
  const records: { head: string; name: string; seq: string }[] = [];
  let cur: { head: string; name: string; seq: string } | null = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith(">")) {
      const head = line.slice(1).trim();
      const name = head.split(/\s+/)[0] ?? "";
      if (!name) return { error: "A '>' line names no construct." };
      cur = { head, name, seq: "" };
      records.push(cur);
      continue;
    }
    if (!cur) {
      const name = val("refname").trim();
      if (!name) return { error: "Name this sequence, or start it with a '>' line." };
      if (/\s/.test(name)) return { error: "A construct's name has no spaces." };
      cur = { head: name, name, seq: "" };
      records.push(cur);
    }
    const seq = line.replace(/\s+/g, "");
    if (!FASTA_BASES.test(seq)) {
      const bad = seq.match(/[^ACGTUacgtu]/)![0];
      return { error: `${cur.name}: '${bad}' is not A, C, G, T or U.` };
    }
    cur.seq += seq;
  }
  const seen = new Set<string>();
  for (const r of records) {
    if (!r.seq) return { error: `${r.name} has no sequence.` };
    if (seen.has(r.name)) return { error: `${r.name} is named twice.` };
    seen.add(r.name);
  }
  const wrap = (q: string) => q.match(/.{1,60}/g)!.join("\n");
  const fasta = records.map(r =>
    `>${r.head}\n${wrap(r.seq.toUpperCase().replace(/U/g, "T"))}`).join("\n") + "\n";
  return { fasta, records: records.map(r => [r.name, r.seq.length] as [string, number]) };
}
const pastedOk = () => { const p = pastedFasta(); return !!p && !("error" in p); };
const pastedText = () => { const p = pastedFasta(); return p && !("error" in p) ? p.fasta : ""; };
/** What a pasted reference holds, said for the hint, the review and the run card. */
function pastedSummary(): string {
  const p = pastedFasta();
  if (!p || "error" in p) return "";
  const names = p.records.map(([n, len]) => `${n} (${num(len)} nt)`);
  return `${p.records.length} record${p.records.length === 1 ? "" : "s"}: ${names.join(", ")}`;
}

const REF_HINT = $("refhint").textContent!;
function renderRefHint() {
  const hint = $("refhint");
  const p = refMode === "paste" ? pastedFasta() : null;
  hint.classList.toggle("err", !!p && "error" in p);
  hint.textContent = refMode === "file" ? REF_HINT
    : !p ? "Paste one or more FASTA records, or one sequence and its name."
    : "error" in p ? p.error : `${pastedSummary()}. Saved as a FASTA file beside the run's logs; U as T.`;
}

function setRefMode(m: "file" | "paste") {
  refMode = m;
  for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>("[data-refmode]")))
    b.setAttribute("aria-checked", String(b.dataset.refmode === m));
  ($("reffile") as HTMLElement).hidden = m !== "file";
  ($("refpaste") as HTMLElement).hidden = m !== "paste";
  renderRefHint();
  syncFlow();
  /* A file's reference is compared with the earlier run's before Start; a pasted one
     only at Start. */
  void checkRunDir();
}
for (const b of Array.from(document.querySelectorAll<HTMLButtonElement>("[data-refmode]")))
  b.addEventListener("click", () => setRefMode(b.dataset.refmode as "file" | "paste"));
for (const id of ["reftext", "refname"])
  $(id).addEventListener("input", () => { renderRefHint(); syncFlow(); });

/* The run's arm, as its two cards say it. m1Ψ by default: the product is for
   m1Ψ-substituted mRNA (2026-10-28). */
function armValue(): "m1psi" | "U" {
  return ($("m1psi") as HTMLInputElement).checked ? "m1psi" : "U";
}

/** The run's modification as the Modification step's two cards say it; the review card,
 *  the run line and Details name it as the step does. Anything else is shown as given. */
function modificationText(arm: unknown): string {
  const a = String(arm ?? "").trim();
  return a.toLowerCase() === "m1psi" ? "m1Ψ-substituted" : a.toLowerCase() === "u" ? "Canonical U" : a;
}

/** The review card: what the run gets, field by field, a blank resolved as Core will. */
function reviewRows(): [string, string][] {
  const rows: [string, string][] = [["Run", KIND_TEXT[kind].title]];
  /* A BAM run of MinKNOW's folders that has failed-read files: whether they are in.
     bam_fail/ picked by itself is the failed reads only (Core's pass_included false). */
  const failed = (readsBam(kind) && inputInfo?.bam && inputInfo.bam.fail.inputs > 0)
    ? (inputInfo.bam.pass_included === false ? ", failed reads only"
      : inputInfo.bam.fail_included ? ", failed reads included" : ", failed reads not included") : "";
  const found = inputInfo?.use ? ` (${num(inputInfo.inputs)} ${readsBam(kind) ? "BAM" : "POD5"} files${
    kind === "live" ? " so far" : ""}${failed})` : "";
  rows.push(["Input", (eff("input", "input_dir") || "missing") + found]);
  rows.push(["Reference", refMode === "paste" ? (pastedOk() ? `pasted, ${pastedSummary()}` : "missing")
    : eff("ref", "reference") || "missing"]);
  rows.push(["Modification", modificationText(armValue())]);
  // The sample as declared, typed or the config's: the rows the report will print.
  const declared = SAMPLE.map(([id, key, name]): [string, string] => [name, eff(id, key)])
    .filter(([, v]) => v);
  rows.push(...(declared.length ? declared : [["Sample", "none declared"] as [string, string]]));
  if (!readsBam(kind)) {
    const sel = $("model") as HTMLSelectElement;
    rows.push(["Model", sel.value ? sel.selectedOptions[0].text
      : cfgModel ? `${base(cfgModel)} (config)` : "none set"]);
  }
  rows.push(["Output", eff("output", "output_dir") || "not set"]);
  rows.push(["Run name", startedAs ?? ((eff("runname", "output_name") || "a new run")
    + (runDirState?.refusal ? " (refused: the run of this name is of another input)"
      : runDirState?.exists ? " (continues the earlier run of this name)" : ""))]);
  if (val("config").trim()) rows.push(["Configuration", base(val("config").trim())]);
  return rows;
}

function renderReview() {
  $("review").innerHTML = reviewRows()
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
}

/* An edit describes the NEXT run, so the review stops naming the last one. */
$("setupfields").addEventListener("change", () => { startedAs = null; syncFlow(); });
$("setupfields").addEventListener("input", () => syncFlow());

/* The facts the run card shows, frozen when the run starts. */
let runFacts: [string, string][] = [];
let startedAs: string | null = null;
/* What Core says of the folder a Start would write (checkRunDir), and its ticket. */
let runDirState: any = null;
let runDirGen = 0;
/* The card the running run was started from: the start flow may move on to the next. */
let runKind: Kind | null = null;
/* How many inputs Core found in the run's folder before it started (describe_input),
   shown until the run's own run_status.json arrives: a POD5 run starts its basecall
   server first, which can take a minute. */
let runInputsFound: number | null = null;

$("go").addEventListener("click", async () => {
  clearRun();
  $("starterr").hidden = true;
  let started = "";
  try {
    started = await invoke<string>("start_qc", {
      inputDir: val("input"),
      // A file's path, or the pasted text as FASTA; Rust writes the text to a file.
      reference: refMode === "file" ? val("ref") : "", outputDir: val("output"),
      referenceText: refMode === "paste" ? pastedText() : "",
      // From the card. Whether pod5 mode needs a model is Core's rule.
      mode: modeOf(kind),
      // Blank means "let Core name it"; Rust drops it and Core names the run.
      runName: val("runname"),
      // A model path from Device's list or picked for this run, or "" for the config's.
      basecallModel: val("model"),
      // Passed as a path, not as its contents: Core reads it.
      config: val("config"),
      realtime: isRealtime(),
      // The m1Ψ box: every construct's arm, sent as --arm.
      arm: armValue(),
      // The sample as declared; Rust sends each only when typed (blank = the config's).
      manufacturer: val("manufacturer"), product: val("product"),
      batchId: val("batchid"), sampleId: val("sampleid"),
      manufactureDate: val("mfgdate"), operator: val("operator"),
    });
  } catch (e) {
    $("starterr").textContent = `Could not start: ${e}`;
    $("starterr").hidden = false;
    return;
  }
  startedAs = started;
  runKind = kind;
  runInputsFound = inputInfo?.use ? inputInfo.inputs : null;
  runFacts = [];
  if (!readsBam(kind)) {
    const sel = $("model") as HTMLSelectElement;
    runFacts.push(["Model", sel.value ? sel.selectedOptions[0].text : (cfgModel ? base(cfgModel) : "from config")]);
  }
  runFacts.push(["Modification", modificationText(armValue())]);
  runFacts.push(["Reference", refMode === "paste"
    ? `pasted (${(pastedFasta() as any)?.records?.map((r: [string, number]) => r[0]).join(", ") ?? ""})`
    : base(eff("ref", "reference"))]);
  ($("setupfields") as HTMLFieldSetElement).disabled = true;
  showPage("run");
  if (timer === undefined) timer = setInterval(tick, 1000) as unknown as number;
  void tick();
});

/* --- the path pickers -----------------------------------------------------------------
   The kind of picker lives on the button as `data-pick`. A cancelled dialog returns null
   and must leave the field alone: Reference has no default.
   `dialog:default` must be in src-tauri/capabilities/default.json. */
const PICKERS: Record<string, { directory: boolean; filters?: { name: string; extensions: string[] }[] }> = {
  dir:   { directory: true },
  file:  { directory: false },
  fasta: { directory: false,
           filters: [{ name: "Reference FASTA", extensions: ["fasta", "fa", "fna", "gz"] }] },
  yaml:  { directory: false,
           filters: [{ name: "Config YAML", extensions: ["yaml", "yml"] }] },
  // A model FOLDER, for this run only. It lands in the Model list, not in a field.
  model: { directory: true },
};

for (const button of Array.from(document.querySelectorAll<HTMLButtonElement>("button.pick"))) {
  button.addEventListener("click", async () => {
    const field = $(button.dataset.for!) as HTMLInputElement | HTMLSelectElement;
    try {
      const chosen = await open({ multiple: false, ...PICKERS[button.dataset.pick!] });
      if (typeof chosen === "string") {
        /* A select ignores a value it has no option for, so a picked model folder
           becomes an option instead. */
        if (field instanceof HTMLSelectElement) await pickModel(chosen);
        else {
          field.value = chosen;
          field.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
    } catch (e) {
      $("starterr").textContent = `could not open a file dialog: ${e}`;
      $("starterr").hidden = false;
    }
  });
}

/* --- what a blank field will actually do ----------------------------------------------
   `--print-config` resolves the config and creates nothing, so it is asked on every edit.
   Shown as PLACEHOLDERS, never values: writing the resolved values into the fields would
   make the GUI a second source for them. */
const PREFILL: [string, string][] = [
  ["input",   "input_dir"],
  ["ref",     "reference"],
  ["output",  "output_dir"],
  ["runname", "output_name"],
  // A config's sample: shows grey under each declared field.
  ...SAMPLE.map(([id, key]): [string, string] => [id, key]),
];
/* Captured before anything overwrites them; these are the sentences in index.html. */
const AUTHORED: Record<string, string> = Object.fromEntries(
  PREFILL.map(([id]) => [id, ($(id) as HTMLInputElement).placeholder]));
const CONFIG_HINT = $("confighint").textContent!;

/* Each lookup takes a ticket and drops its result if a newer one was issued, so an
   overtaken answer never lands last. */
let configGen = 0;
/* The last resolved config, for a blank field's value and the Overview's gates. */
let resolvedCfg: any = null;
let cfgModel = "";

/* "this config sets sample.arm = m1psi · sample.arms: 15 entries". Keys under one parent
   past three are counted, not listed; a long value keeps its end. */
function describeChanges(changes: Record<string, unknown>): string {
  const keys = Object.keys(changes);
  if (!keys.length) return "this config changes nothing from the defaults";
  const groups = new Map<string, string[]>();
  for (const k of keys) {
    const parent = k.includes(".") ? k.slice(0, k.lastIndexOf(".")) : "";
    groups.set(parent, [...(groups.get(parent) ?? []), k]);
  }
  const parts: string[] = [];
  for (const [parent, ks] of groups) {
    if (parent && ks.length > 3) parts.push(`${parent}: ${ks.length} entries`);
    else for (const k of ks) parts.push(`${k} = ${shortValue(changes[k])}`);
  }
  return `this config sets ${parts.join(" · ")}`;
}

function shortValue(v: unknown): string {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 40 ? "…" + s.slice(-39) : s;
}

async function refreshConfig() {
  const gen = ++configGen;
  const named = val("config").trim() !== "";
  let resolved: any;
  let changes: Record<string, unknown> = {};
  try {
    /* Output dir and Run name blank here, so the placeholders below stay the config's
       values; checkRunDir asks with them typed. */
    const answer = await invoke<any>("read_config",
      { config: val("config"), outputDir: "", runName: "", inputDir: "", reference: "",
        mode: "", live: false });
    if (gen !== configGen) return;
    resolved = answer["resolved_config"];
    /* read_config passes Device's layer first and the operator's file last. */
    const layers = answer["config_changes"] ?? [];
    if (named && layers.length) changes = layers[layers.length - 1]["changes"];
  } catch (e) {
    if (gen !== configGen) return;
    /* With a config named, the failure is most likely the config's, and is said beside
       it. With none, Core itself could not be asked: the shell's own message, which says
       where it looked for the interpreter, goes over the start flow. (Until 2026-11-14 a
       Core repo field on Settings › Advanced answered this; the packaged app never read
       it.) */
    for (const [id] of PREFILL) ($(id) as HTMLInputElement).placeholder = AUTHORED[id];
    $("confighint").textContent = named ? String(e) : CONFIG_HINT;
    $("confighint").className = named ? "dim err" : "dim";
    $("corebanner").textContent = String(e);
    $("corebanner").hidden = named;
    resolvedCfg = null;
    cfgModel = "";
    syncFlow();
    return;
  }
  $("corebanner").hidden = true;
  resolvedCfg = resolved;
  /* The box goes as a flag, and a flag beats the config, so a config that names the
     run's arm sets the box. */
  const cfgArm = String(dig(resolved, "sample.arm") ?? "").trim().toLowerCase();
  if (cfgArm === "m1psi" || cfgArm === "u")
    ($(cfgArm === "m1psi" ? "m1psi" : "armu") as HTMLInputElement).checked = true;
  /* What the named config CHANGES, from Core (I3). */
  $("confighint").textContent = named ? describeChanges(changes) : CONFIG_HINT;
  $("confighint").className = "dim";
  for (const [id, path] of PREFILL) {
    const v = dig(resolved, path);
    ($(id) as HTMLInputElement).placeholder =
      v === null || v === undefined || v === "" ? AUTHORED[id] : `${v}  (from config)`;
  }
  cfgModel = String(dig(resolved, "basecall.model") ?? "");
  ($("model") as HTMLSelectElement).options[0].textContent =
    cfgModel ? `From the config: ${cfgModel}` : "From the config (none set)";
  /* A run config is more specific than Device and wins. Say so when it happens. */
  const over: string[] = [];
  for (const [id, key] of [["server", "binary"], ["device", "device"]] as const) {
    const saved = val(id).trim();
    const used = dig(resolved, `basecall.server.${key}`);
    if (saved && used && used !== saved) over.push(`${id} = ${used}`);
  }
  $("machinenote").className = "dim";
  $("machinenote").textContent = over.length
    ? `This run's config overrides ${over.join(", ")}.` : "";
  syncFlow();
}

$("config").addEventListener("change", refreshConfig);

/* --- whether Start continues an earlier run (2026-11-23) ------------------------------
   A Run name that names a folder already in Output dir CONTINUES that run, and nothing
   said so, before or during the run ("it's hard to know if the run is continued or not").
   Core describes the folder a Start would write (`run_dir_state`, from the same
   --print-config, given Output dir and Run name as start_qc passes them); the hint under
   Run name and the review say what Start will do, and the run card what was carried over
   (run_status.json's `continued`). `runDirState` is declared with the run's other state,
   above reviewRows' first call. */
const RUNNAME_HINT = $("runnamehint").textContent!;
/* Core's run_dir_error_code -> what the window says, and whether it is a mistake (red).
   No folder yet is not one: the step's own marker already asks for it. */
const RUN_DIR_WORDS: Record<string, [string, boolean]> = {
  no_output_dir: ["Choose an output folder.", false],
  run_name_not_one_folder: [
    "A run name is one folder name: no / in it, and not . or .. on its own.", true],
};
const plural = (k: number, w: string) => `${k} ${w}${k === 1 ? "" : "s"}`;

async function checkRunDir() {
  const gen = ++runDirGen;
  let answer: any;
  try {
    /* What Start would send (start_qc's own values), so Core can say whether the run
       there would refuse it. A pasted reference has no file yet: sent blank, and Core
       compares it at Start. */
    answer = await invoke<any>("read_config",
      { config: val("config"), outputDir: val("output"), runName: val("runname"),
        inputDir: val("input"), reference: refMode === "file" ? val("ref") : "",
        mode: modeOf(kind), live: isRealtime() });
  } catch {
    /* refreshConfig says why Core could not be asked, over the flow; this hint falls
       back to its general sentence rather than guess. */
    if (gen !== runDirGen) return;
    runDirState = null;
    renderRunNameHint();
    return;
  }
  if (gen !== runDirGen) return;
  runDirState = answer["run_dir_error"]
    ? { error: String(answer["run_dir_error"]), code: answer["run_dir_error_code"] ?? null }
    : (answer["run_dir_state"] ?? null);
  renderRunNameHint();
  syncFlow();
}

/** How far the earlier run got, from what it left: "12 of 40 inputs, stopped". */
function earlierRunText(s: any): string {
  const got = s.inputs_processed == null ? ""
    : s.inputs_total != null ? `${s.inputs_processed} of ${plural(s.inputs_total, "input")}`
    : `${plural(s.inputs_processed, "input")} done`;
  const ended = s.report === "run_end" ? "finished" : s.report === "user_abort" ? "stopped"
    : s.report ? String(s.report) : "no final report";
  return [got, ended].filter(Boolean).join(", ");
}

/** What Start carries over on this card. A live run restores what its manifest holds;
 *  an offline run aligns and counts every input again, reusing a POD5's basecall. */
function carriedText(s: any): string {
  if (isRealtime()) return s.inputs_on_record
    ? `the ${plural(s.inputs_on_record, "input")} on its record ${s.inputs_on_record === 1 ? "is" : "are"} restored, not processed again`
    : "nothing is on its record to restore, so every input is processed again";
  if (!readsBam(kind) && s.basecalls_cached)
    return `every input is processed again, reusing the ${plural(s.basecalls_cached, "basecall")} on record where the POD5 file and the model are unchanged`;
  return "every input is processed again";
}

function renderRunNameHint() {
  const el = $("runnamehint");
  const s = runDirState;
  if (s?.error) {
    /* Core's sentence points at flags ("Pass --output-dir <dir>"), which the window does
       not have: the refusals it can reach are said in its own words, by Core's code
       (2026-11-23). An unknown one is shown as Core wrote it. */
    const own = RUN_DIR_WORDS[s.code];
    el.textContent = own ? own[0] : s.error;
    el.className = own && !own[1] ? "dim" : "dim err";
    return;
  }
  /* Core's own words for why the run there would refuse this Start: another input or
     reference (run_input.py). Start stays off (missingOnStep). */
  if (s?.refusal) {
    el.textContent = `${s.refusal} Start is off until the run name or the input changes.`;
    el.className = "dim err";
    return;
  }
  if (!s?.exists) {
    el.textContent = s ? "A new run. The name of an earlier run here would continue it."
      : RUNNAME_HINT;
    el.className = "dim";
    return;
  }
  const unread = s.unreadable?.length ? ` Could not read: ${s.unreadable.join("; ")}.` : "";
  const later = s.not_compared?.includes("reference")
    ? " The pasted reference is compared with the earlier run's at Start." : "";
  el.textContent = `Continues the earlier run of this name (${earlierRunText(s)}): `
    + `${carriedText(s)}. Its report is replaced. For a separate run, give another name.`
    + later + unread;
  el.className = "dim warn";
}

/* Asked again whenever what Start would send can change: while typing, a moment after
   the last key (the line read "A new run" for a name being typed over an earlier run's,
   2026-11-23, since it was asked only when a field lost focus); at once on a change, a
   pick, a card, or the folder Core moved the input to (describeInput). */
let runDirTimer: number | undefined;
function checkRunDirSoon() {
  clearTimeout(runDirTimer);
  runDirTimer = setTimeout(() => void checkRunDir(), 300) as unknown as number;
}
for (const id of ["output", "runname", "input", "ref"]) {
  $(id).addEventListener("input", checkRunDirSoon);
  $(id).addEventListener("change", () => void checkRunDir());
}
$("config").addEventListener("change", () => void checkRunDir());

/** The run card's line for a run that continued an earlier one, or null for a new run. */
function continuedText(c: any): string | null {
  if (!c) return null;
  if (c.inputs_restored != null)
    return `the earlier run of this name · ${plural(c.inputs_restored, "input")} restored`;
  return `the earlier run of this name · every input processed again` +
    (c.basecalls_cached ? `, ${plural(c.basecalls_cached, "basecall")} on record` : "");
}

// =====================================================================================
// Settings › Device (was This Mac): facts about the computer, saved by the app and passed
// to Core as the first config layer. Processor is the server's --device, as a menu.
// =====================================================================================
const MACHINE = ["server", "device", "models"];
/* GPU is `metal` on a Mac and `cuda:0` elsewhere. Auto leaves --device unset, so the
   server picks; nothing chooses a device for the operator. */
const GPU_DEVICE = /Mac/.test(navigator.userAgent) ? "metal" : "cuda:0";
const SKIP_KEY = "mrnaspect.device.skipped";

function setProcessor(saved: string) {
  const gpu = $("gpuopt") as HTMLOptionElement;
  /* A saved GPU value other than the default (cuda:1, say) round-trips unchanged. */
  gpu.value = saved && saved !== "cpu" ? saved : GPU_DEVICE;
  ($("device") as HTMLSelectElement).value = saved;
}

const machineIsSet = () => !!(val("server").trim() || val("models").trim());

type Model = { name: string; path: string };
/* Device's models and the ones picked in the start flow, kept apart: a Device refresh
   must not drop a model the operator picked for this run. */
let macModels: Model[] = [];
const picked: Model[] = [];
const MODEL_HINT = $("modelhint").textContent!;

function fillModels() {
  const sel = $("model") as HTMLSelectElement;
  const keep = sel.value;
  sel.querySelectorAll("optgroup").forEach(g => g.remove());
  const groups: [string, Model[]][] = [
    ["Device", macModels],
    ["Picked for this run", picked.filter(p => !macModels.some(m => m.path === p.path))],
  ];
  for (const [label, list] of groups) {
    if (!list.length) continue;
    const group = document.createElement("optgroup");
    group.label = label;
    for (const m of list) group.appendChild(new Option(m.name, m.path));
    sel.appendChild(group);
  }
  if (Array.from(sel.options).some(o => o.value === keep)) sel.value = keep;
}

async function refreshModels() {
  const dir = val("models").trim();
  try {
    macModels = await invoke<Model[]>("list_models", { modelsDir: dir });
    $("modelhint").className = "dim";
    /* An empty list has two causes, and each has a different fix. */
    $("modelhint").textContent = macModels.length ? MODEL_HINT
      : !dir ? "No models folder set in Device. Set one, or Browse… for one model folder."
      : `No model folder in ${dir} or directly inside it (a dorado model folder holds a `
        + "config.toml). An ONT server bundle keeps its models a level down, e.g. in models/";
  } catch (e) {
    macModels = [];
    $("modelhint").className = "dim err";
    $("modelhint").textContent = String(e);
  }
  fillModels();
  syncFlow();
}

/* A folder picked on the Model row: one model, for this run. A folder that is not one
   is said so here, before Start, rather than by the server after it has started. */
async function pickModel(path: string) {
  let found: Model[] = [];
  try {
    found = await invoke<Model[]>("list_models", { modelsDir: path });
  } catch (e) {
    $("modelhint").className = "dim err";
    $("modelhint").textContent = String(e);
    return;
  }
  if (found.length !== 1) {
    $("modelhint").className = "dim err";
    $("modelhint").textContent = found.length
      ? `${path} holds ${found.length} models: Browse to one of them, or set it as the `
        + "Device models folder to list them all"
      : `${path} is not a dorado model folder (it holds no config.toml), and none is `
        + "directly inside it";
    return;
  }
  const m = found[0];
  if (!macModels.some(x => x.path === m.path) && !picked.some(x => x.path === m.path)) {
    picked.push(m);
  }
  fillModels();
  ($("model") as HTMLSelectElement).value = m.path;
  $("modelhint").className = "dim";
  $("modelhint").textContent = MODEL_HINT;
  $("model").dispatchEvent(new Event("change", { bubbles: true }));
}

async function saveMachine() {
  try {
    await invoke("save_machine", { server: val("server"), device: val("device"),
                                   modelsDir: val("models") });
  } catch (e) {
    $("machinenote").className = "dim err";
    $("machinenote").textContent = `Could not save: ${e}`;
    return;
  }
  await refreshModels();
  await refreshConfig();
}

/** Loads Device; true when nothing is saved yet (first launch). */
async function loadMachine(): Promise<boolean> {
  let first = false;
  try {
    const m = await invoke<{ server: string; device: string; models_dir: string }>(
      "load_machine");
    ($("server") as HTMLInputElement).value = m.server;
    setProcessor(m.device);
    ($("models") as HTMLInputElement).value = m.models_dir;
    first = !(m.server || m.device || m.models_dir);
  } catch (e) {
    /* A settings file that does not parse is shown, not replaced. */
    $("machinenote").className = "dim err";
    $("machinenote").textContent = String(e);
    first = true;
  }
  await refreshModels();
  return first;
}

for (const id of MACHINE) $(id).addEventListener("change", saveMachine);
$("savedevice").addEventListener("click", async () => { await saveMachine(); showPage("start"); });
/* BAM needs none of this, so first launch must not block on it. Remembered per viewer. */
$("skipdevice").addEventListener("click", () => {
  try { localStorage.setItem(SKIP_KEY, "1"); } catch { /* storage refused: asks again next launch */ }
  showPage("start");
});

// =====================================================================================
// About
// =====================================================================================
async function renderAbout() {
  const rows: [string, string][] = [];
  try { rows.push(["mRNAspect", await getVersion()]); } catch { rows.push(["mRNAspect", "unknown"]); }
  const prov = await invoke<any>("read_provenance").catch(() => null);
  if (prov?.software_version) rows.push(["Analysis engine (last run)", String(prov.software_version)]);
  const server = dig(prov, "params.dorado_server");
  if (server) rows.push(["Basecall server (last run)", typeof server === "string" ? server
    : String(server.version ?? JSON.stringify(server))]);
  $("about").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
}

/* The file staging writes into the bundle (scripts/make_notices.py), in the system's
   viewer. A build without it says so here, beside the button. */
$("opennotices").addEventListener("click", async () => {
  const err = $("noticeserr");
  try { await invoke("open_notices"); err.hidden = true; }
  catch (e) { err.textContent = String(e); err.hidden = false; }
});

// =====================================================================================
// The run view: run card, a row of panel names, one panel at a time.
// =====================================================================================
/* Panel keys are Core's (reporter.FIGURE_PANELS), in its order, after the Overview.
   test_gui_panels.py holds the two lists equal. */
const PANELS: [string, string][] = [
  ["overview", "Overview"],
  ["read-length", "Read length"],
  ["polya", "Poly(A)"],
  ["quality", "Quality"],
  ["constructs", "Constructs"],
  ["identity", "Identity"],
  ["full-length", "Full length"],
  ["flow-cell", "Sequencing information"],
];
/** Why a chart is not there. Core writes the charts on every render, and the first
 *  interim comes once the run passes min_reads (main.py); a run that ended without them
 *  stopped before its report. */
function noChartsYet(): string {
  return lastPoll && isActive(lastPoll)
    ? `Charts after ${num(minReads())} reads`
    : "No charts: the run ended before its report";
}
const NEEDS_MINKNOW = "Needs MinKNOW files";
let current = "overview";
let lastPoll: Poll | null = null;
let figures: Figure[] = [];
let timer: number | undefined;

const isActive = (r: Poll) => r.state === "running" || r.state === "stopping";

function instrumentAvailable(p: any) {
  const i = p?.instrument;
  return !!(i && (i.pore_activity?.available || i.end_reason?.available || i.voltage?.available));
}

/** The wait the Overview's top row shows, said by the panels it opens too
 *  (2026-11-13: the tabs drew their charts before the Overview's 1,000 reads); "" once
 *  the run has them. A run that ended short waits for nothing, and says so. */
function readsWait(p: any): string {
  if ((p?.mapped_reads ?? 0) >= minReads()) return "";
  return lastPoll && isActive(lastPoll) ? `Waiting for ${num(minReads())} mapped reads`
    : `Fewer than ${num(minReads())} mapped reads`;
}
/* The panels the Overview's top row opens: they wait for its reads. */
const MIN_GATED = new Set(["read-length", "polya", "quality", "constructs", "identity", "full-length"]);

/** Why a panel has nothing to show yet, or "" when it has something. */
function panelReason(key: string): string {
  const p = lastPoll?.panel;
  const figs = figures.filter(f => f.panel === key);
  if (key === "overview") return "";
  if (key === "flow-cell") return instrumentAvailable(p) || figs.length ? "" : NEEDS_MINKNOW;
  /* Before anything else is asked: a chart file from any render used to open every panel
     (2026-11-14). The flow cell, above, is MinKNOW's record and waits for no reads. */
  if (MIN_GATED.has(key)) { const w = readsWait(p); if (w) return w; }
  if (figs.length) return "";
  // Live content: the whole-run histograms, the construct table.
  if (key === "read-length") return p?.distributions?.read_length?.bin_counts ? "" : noChartsYet();
  if (key === "polya") return p?.distributions?.polya?.bin_counts ? "" : noChartsYet();
  if (key === "quality") return p?.distributions?.quality_score?.bin_counts ? "" : noChartsYet();
  if (["constructs", "identity", "full-length"].includes(key))
    return Object.keys(p?.distributions?.constructs ?? {}).length ? "" : noChartsYet();
  return noChartsYet();
}

/* The tab row follows the WAI-ARIA tabs pattern: one tablist, a tab per panel, the
   selected one in the tab order, ← → to step. A panel with nothing yet stays, greyed,
   its reason on hover. */
function renderTabs() {
  const i = PANELS.findIndex(([k]) => k === current);
  $("tabs").innerHTML =
    `<button class="iconbtn" id="prevpanel" aria-label="Previous panel">
       <svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg></button>` +
    PANELS.map(([k, name]) => {
      const why = panelReason(k);
      const sel = k === current;
      return `<button role="tab" id="tab-${k}" data-panel="${k}" aria-controls="panels"
        aria-selected="${sel}" tabindex="${sel ? 0 : -1}"${why ? ` class="empty" title="${esc(why)}"` : ""}>${esc(name)}</button>`;
    }).join("") +
    `<span class="spacer"></span><span class="pos">${i + 1} / ${PANELS.length}</span>
     <button class="iconbtn" id="nextpanel" aria-label="Next panel">
       <svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg></button>`;
  for (const b of Array.from($("tabs").querySelectorAll<HTMLButtonElement>("[role=tab]")))
    b.addEventListener("click", () => selectPanel(b.dataset.panel!));
  $("prevpanel").addEventListener("click", () => stepPanel(-1));
  $("nextpanel").addEventListener("click", () => stepPanel(1));
  $("panels").setAttribute("role", "tabpanel");
  $("panels").setAttribute("aria-labelledby", `tab-${current}`);
}

function selectPanel(key: string) {
  current = key;
  renderCurrentPanel();
  (document.getElementById(`tab-${key}`) as HTMLElement | null)?.focus();
}

function stepPanel(d: number) {
  const i = PANELS.findIndex(([k]) => k === current);
  selectPanel(PANELS[(i + d + PANELS.length) % PANELS.length][0]);
}

document.addEventListener("keydown", e => {
  if (page !== "run" || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
  const t = e.target as HTMLElement;
  if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) return;
  e.preventDefault();
  stepPanel(e.key === "ArrowLeft" ? -1 : 1);
});

function renderCurrentPanel() {
  if (!lastPoll || lastPoll.state === "idle") return;
  renderTabs();
  const p = lastPoll.panel;
  const el = $("panels");
  // The panel's result type, so a mark the page draws there wears its colour code.
  el.dataset.result = current;
  // Release the previous panel's charts before their nodes are replaced.
  Array.from(el.querySelectorAll(".fig")).forEach(n => Plotly.purge(n));
  // The Overview alone moves (R5): a panel's chart is Plotly's, and redrawing it resets its zoom.
  if (current === "overview") { el.innerHTML = renderOverview(p, lastPoll); wireOverview(); animate(el); return; }
  const why = panelReason(current);
  if (why) { el.innerHTML = `<p class="empty">${esc(why)}</p>`; return; }
  if (current === "flow-cell") { el.innerHTML = renderFlowCell(p); drawFigures(el); return; }
  if (current === "quality") { el.innerHTML = renderQuality(p); drawFigures(el); return; }
  if (current === "constructs") { el.innerHTML = renderConstructs(p); drawFigures(el); return; }
  // Read length: Core's figures alone, its own construct dropdown kept (2026-11-06).
  // Its figures side by side in one window (2026-11-13).
  if (current === "read-length") {
    el.innerHTML = figures.length ? panelFigures("read-length", singleSkip(p), [], false, true)
      : hist(p?.distributions?.read_length, "nt");
    drawFigures(el); return;
  }
  el.innerHTML = renderTablePanel(current, p);
  wireTablePanel(current, p);
}

// --- the run card ----------------------------------------------------------------------

const two = (n: number) => String(n).padStart(2, "0");
function fmtDur(s: number | null) {
  if (s == null) return NOT_MEASURED;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${two(m)}m` : `${m}m ${two(s % 60)}s`;
}
const fmtClock = (t: number) => { const d = new Date(t * 1000); return `${two(d.getHours())}:${two(d.getMinutes())}`; };

function renderRunCard(r: Poll) {
  $("norun").hidden = true;
  $("runcard").hidden = false;
  $("runtitle").textContent = startedAs ?? "Current run";
  const res = r.result ?? {};
  const pill = $("runpill");
  /* The run ended: lead with WHY. A cancelled run used to read exactly like a completed
     one. */
  const [label, cls] = r.state === "running" ? ["Running", "running"]
    : r.state === "stopping" ? ["Stopping", "running"]
    : res.reason === "user_abort" ? ["Stopped", "bad"]
    : res.status === "error" ? ["Failed", "bad"] : ["Finished", "done"];
  pill.className = `pill ${cls}`;
  pill.querySelector("span")!.textContent = label;
  const p = r.panel ?? {};
  /* run_status.json first: written from the run's first moment, so Inputs has a count
     before any input finishes (2026-11-12). A live MinKNOW run has no total, so it
     says what MinKNOW has written so far. */
  const st = r.status ?? {};
  const done = st.inputs_processed ?? p.inputs_processed ?? res.inputs_processed;
  const total = st.inputs_total ?? p.inputs_total ?? res.inputs_total;
  const found = st.inputs_found;
  const early = runInputsFound == null ? NOT_MEASURED
    : runKind === "live" ? `0 done · ${runInputsFound} found` : `0 of ${runInputsFound}`;
  const inputs = done == null ? early : total != null ? `${done} of ${total}`
    : found != null ? `${done} done · ${found} found` : String(done);
  const meta: [string, string][] = [["Elapsed", fmtDur(r.elapsed_s)], ["Inputs", inputs]];
  /* Staleness is shown ALWAYS while running: it is what tells "nothing new" from "the run
     has stalled". */
  if (isActive(r)) meta.push(["Updated", r.age_s == null ? "no input yet" : `${r.age_s} s ago`]);
  else if (r.finished_at != null) meta.push(["Finished", fmtClock(r.finished_at)]);
  if (r.state === "stopping" && r.stopping_for_s != null)
    meta.push(["Stopping", `${r.stopping_for_s} s`]);
  $("runmeta").innerHTML = meta.map(([k, v]) =>
    `<span class="kv strong"><span>${esc(k)}</span><span>${esc(v)}</span></span>`).join("");
  /* Said first and in the warning colour: a continued run's numbers include the earlier
     run's inputs, and its report replaces the one that was there (2026-11-23). */
  const cont = continuedText(st.continued);
  $("runfacts").innerHTML =
    (cont ? `<span class="kv warn"><span>Continues</span><span>${esc(cont)}</span></span>` : "") +
    runFacts.map(([k, v]) =>
    `<span class="kv"><span>${esc(k)}</span><span>${esc(v)}</span></span>`).join("") +
    (res.error ? `<span class="err">${esc(res.error)}</span>` : "") +
    `<button class="linkbtn" id="opendetails">Details</button>`;
  /* Its own id: the dialog's list is #details, and of two elements with one id
     getElementById returns the first in the page, which was this button (2026-11-14: the
     rows were written into the run card and the dialog opened empty). */
  $("opendetails").addEventListener("click", openDetails);
  const stop = $("stop") as HTMLButtonElement;
  stop.disabled = r.state !== "running";
  /* While running, the report is the interim one Core re-renders every few inputs (an
     HTML page); at the end, the PDF. The label says which it opens (2026-11-12: the
     report and the folder button read as one thing). Only the label's span is set, so
     the icon stays. */
  const rep = String(res.report ?? "").toLowerCase();
  $("openreplabel").textContent = isActive(r) ? "View interim report"
    : !rep || rep.endsWith(".pdf") ? "View PDF report" : "View report";
  ($("openrep") as HTMLButtonElement).disabled = isActive(r) ? r.age_s == null : !res.report;
  ($("reveal") as HTMLButtonElement).disabled = !startedAs;
}

/* The folder button names where it opens the folder: Finder on a Mac. */
$("reveallabel").textContent = /Mac/.test(navigator.userAgent) ? "Show in Finder"
  : /Windows/.test(navigator.userAgent) ? "Show in Explorer" : "Open output folder";

for (const [id, cmd] of [["openrep", "open_report"], ["reveal", "reveal_run_dir"]]) {
  $(id).addEventListener("click", async () => {
    try { await invoke(cmd); }
    catch (e) { $("runfacts").insertAdjacentHTML("beforeend", `<span class="err">${esc(e)}</span>`); }
  });
}

/* Stop asks first. Core's abort is graceful: it takes effect at the next INPUT boundary,
   so a POD5 being basecalled finishes first. On a live run the dialog also says that
   MinKNOW keeps sequencing: a bare "Stop" read as stopping the sequencer (2026-11-12). */
$("stop").addEventListener("click", () => {
  $("stoplive").hidden = runKind !== "live";
  ($("stopdlg") as HTMLDialogElement).showModal();
});
$("stopcancel").addEventListener("click", () => ($("stopdlg") as HTMLDialogElement).close());
$("stopconfirm").addEventListener("click", async () => {
  ($("stopdlg") as HTMLDialogElement).close();
  // SIGTERM to the process group: Core finalizes and still writes a report.
  try { await invoke("cancel_run"); void tick(); }
  catch (e) { $("runfacts").insertAdjacentHTML("beforeend", `<span class="err">cancel failed: ${esc(e)}</span>`); }
});

/* Details: the provenance block of qc_result.json, once Core has written it; until then,
   what the start flow sent. The full record stays in the report. */
/** An ISO time as the report writes one (reporter._clock_text): this computer's clock
 *  and its offset from UTC, `2026-11-10 14:03:27 (UTC+09:00)`. */
function clockText(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const off = -d.getTimezoneOffset(), a = Math.abs(off);
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} `
    + `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())} `
    + `(UTC${off < 0 ? "-" : "+"}${two(Math.floor(a / 60))}:${two(a % 60)})`;
}

async function openDetails() {
  const prov = await invoke<any>("read_provenance").catch(() => null);
  const cfg = dig(prov, "params.config") ?? resolvedCfg ?? {};
  const rows: [string, string][] = [];
  const add = (k: string, v: any) => { if (v != null && v !== "") rows.push([k, String(v)]); };
  const ins = prov?.inputs;
  add("Input", Array.isArray(ins) ? (ins.length === 1 ? base(String(ins[0]?.path ?? ins[0]))
    : `${ins.length} files`) : eff("input", "input_dir"));
  add("Reference", cfg.reference ?? eff("ref", "reference"));
  // A config can set constructs one by one (sample.arms): said beside the run's value.
  const perConstruct = Object.keys(dig(cfg, "sample.arms") ?? {}).length;
  add("Modification", modificationText(dig(cfg, "sample.arm") ?? armValue())
    + (perConstruct ? ` · ${num(perConstruct)} set per construct` : ""));
  add("Model", dig(cfg, "basecall.model"));
  const srv = dig(prov, "params.dorado_server");
  add("Basecall server", srv && typeof srv === "object" ? (srv.version ?? JSON.stringify(srv)) : srv);
  const mm = dig(prov, "tools.minimap2");
  add("Aligner", mm && typeof mm === "object" ? (mm.version ?? JSON.stringify(mm)) : mm);
  if (prov) {
    // The packaged app is not a checkout and has no commit: the version alone, then.
    const commit = String(prov.git_commit ?? "").slice(0, 7);
    add("Analysis engine", [prov.software_version, commit && `${commit}${prov.git_dirty ? " (modified)" : ""}`]
      .filter(Boolean).join(" · "));
  }
  add("Finished", prov?.generated_at ? clockText(String(prov.generated_at)) : null);
  add("Output", startedAs);
  $("details").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  ($("detailsdlg") as HTMLDialogElement).showModal();
}
$("detailsclose").addEventListener("click", () => ($("detailsdlg") as HTMLDialogElement).close());

// --- Overview ----------------------------------------------------------------------------
/* The two gates are the resolved config's (`min_reads`, `min_reads_per_construct`); the
   fallbacks are Core's shipped values, used only when the config could not be read. */
const SHIPPED_MIN_READS = 1000;
const SHIPPED_MIN_READS_PER_CONSTRUCT = 100;
/* A GUI cap, not Core's: Core's analysis.max_constructs is a memory ceiling that stops the
   run, so it stays at 64 and this only hides the smallest constructs here. */
const OVERVIEW_CAP = 25;
const minReads = () => Number(dig(resolvedCfg, "min_reads") ?? SHIPPED_MIN_READS);
const minPerConstruct = () =>
  Number(dig(resolvedCfg, "min_reads_per_construct") ?? SHIPPED_MIN_READS_PER_CONSTRUCT);

/** Each bin's height as a share of the tallest, on the scale the card draws. */
function histHeights(counts: number[], sqrt: boolean): number[] {
  const f = sqrt ? Math.sqrt : (v: number) => v;
  const top = f(Math.max(...counts, 1));
  return counts.map(v => f(v) / top);
}

/** A step histogram in a 300 x 100 box: one path for the line, one for the fill. Drawn
 *  by tween.ts's stepPath, which also draws each frame of a card's animation. */
function histPath(counts: number[], sqrt: boolean) {
  return stepPath(histHeights(counts, sqrt));
}

/** Round tick values over [lo, hi]. */
function niceTicks(lo: number, hi: number, n = 4): number[] {
  const span = hi - lo || 1;
  const raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const stepv = [1, 2, 2.5, 5, 10].map(s => s * mag).find(s => span / s <= n) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / stepv) * stepv; v <= hi + 1e-9; v += stepv) out.push(+v.toFixed(6));
  return out;
}

/* Each label is centred under its own gridline. Only one within EDGE of the box's side is
   pulled in, so it stays inside the card: aligning the first and the last by their place
   in the list ran "1,000" into "1,500 nt" when the window ended well past the last tick
   (seen on a Mac, 2026-11-16). */
function xTicks(vals: number[], lo: number, hi: number, unit: string) {
  const EDGE = 0.08;
  return vals.map((v, i) => {
    const at = (v - lo) / (hi - lo || 1);
    const tx = at < EDGE ? "0" : at > 1 - EDGE ? "-100%" : "-50%";
    return `<span style="left:${(at * 100).toFixed(2)}%; transform:translateX(${tx})">${num(v)}${i === vals.length - 1 ? unit : ""}</span>`;
  }).join("");
}

/** Core's display window for read length and poly(A) (figspec.WINDOW_QUANTILES;
 *  2026-11-09), so a card opens where its panel's chart does. */
const WINDOW_QUANTILES: [number, number] = [0.01, 0.99];
/** The quality card's: from zero to where 99.5% of the reads are. */
const QUALITY_WINDOW: [number, number] = [0, 0.995];

/** The bins drawn, by Core's rule (figspec._quantile_window): from the bin where the
 *  running count first reaches the lower share of the reads to the bin where it reaches
 *  the upper, whole bins, under- and overflow counted, trimmed to occupied bins. A lower
 *  share of 0 keeps the axis from the histogram's start. What lies outside is counted. */
function drawnBins(h: any, q: [number, number] = WINDOW_QUANTILES) {
  const c: number[] = h?.bin_counts ?? [];
  const under = h?.underflow ?? 0, over = h?.overflow ?? 0;
  const total = c.reduce((a, b) => a + b, 0) + under + over;
  const reach = (share: number) => {
    let cum = under;
    for (let i = 0; i < c.length; i++) { cum += c[i]; if (cum >= share * total) return i; }
    return c.length - 1;
  };
  let i0 = 0, i1 = reach(q[1]);
  if (q[0] > 0) { i0 = reach(q[0]); while (i0 < i1 && !c[i0]) i0++; }
  while (i1 > i0 && !c[i1]) i1--;
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);
  const w = h?.bin_width ?? 1, start = h?.bin_min ?? 0;
  return { shown: c.slice(i0, i1 + 1), below: under + sum(c.slice(0, i0)),
           beyond: sum(c.slice(i1 + 1)) + over, lo: start + i0 * w, hi: start + (i1 + 1) * w };
}

/** "+N beyond hi", or "+N outside lo–hi" when reads are cut on both sides. */
function outsideNote(below: number, beyond: number, lo: number, hi: number, unit: string) {
  if (!below && !beyond) return "";
  return below ? `+${num(below + beyond)} outside ${num(lo)}–${num(hi)}${unit}`
               : `+${num(beyond)} beyond ${num(hi)}${unit}`;
}

type TopCard = { title: string; panel: string; head: string; headSub: string;
                 line: string; warn: boolean; warnWhy?: string; h: any; sqrt: boolean;
                 unit: string; corner: string; median: number | null;
                 window: [number, number] };

/* A card opens its panel. A div with a button role, not a <button>: WebKit lays a
   button's content out as one centred box, which pushed the lines under the title down. */
const cardOpen = (panel: string, title: string) =>
  `class="ovcard" data-open="${panel}" data-result="${panel}" role="button" tabindex="0" aria-label="Open the ${esc(title)} panel"`;

/** `wait` is what the card waits for; "" draws the empty frame alone (before the first
 *  result, when one box over the whole Overview says it). */
function topCardHtml(c: TopCard, ready: boolean, wait: string) {
  // Waiting, the chart's frame is drawn and the box sits on it (2026-11-12).
  if (!ready || !c.h?.bin_counts) {
    return `<div ${cardOpen(c.panel, c.title)}><div class="ovhead"><span class="t">${esc(c.title)}</span></div>
      <div class="plotbox">${wait ? `<span class="waittext">${esc(wait)}</span>` : ""}</div>
      <div class="xt"></div></div>`;
  }
  const { shown, below, beyond, lo, hi } = drawnBins(c.h, c.window);
  // The heights ride on the <svg>, so the engine can move the two paths toward them.
  const heights = histHeights(shown, c.sqrt), p = stepPath(heights);
  const X = (v: number) => ((v - lo) / (hi - lo || 1) * 300).toFixed(1);
  const med = c.median != null && c.median >= lo && c.median <= hi
    ? `<path class="hx-med" d="M${X(c.median)} 0V100"/>` : "";
  // A gridline at each labelled x tick (2026-11-12: too few, hard to see).
  const ticks = niceTicks(lo, hi);
  const grid = `<path class="hx-grid" d="${ticks.map(v => `M${X(v)} 0V100`).join("")}"/>`;
  // The line and "+N beyond" sit right under the title and the headline value.
  return `<div ${cardOpen(c.panel, c.title)}>
    <div class="ovhead"><span class="t">${esc(c.title)}</span>
      ${c.warn ? `<span class="uncorr" title="${esc(c.warnWhy || "No poly(A) correction was applied")}">⚠ uncorrected</span>` : ""}
      <span class="spacer"></span><span class="hpair"><span class="hs">${esc(c.headSub)}</span><span class="h">${c.head}</span></span></div>
    <div class="ovsub"><span class="ovline">${c.line}</span></div>
    <div class="plotbox">
      <svg viewBox="0 0 300 100" preserveAspectRatio="none" role="img" aria-label="${esc(c.title)} histogram"
           data-tw="top.${c.panel}.hist" data-h="${heights.map(v => v.toFixed(4)).join(",")}">
        ${grid}<path class="hx-area" d="${p.area}"/><path class="hx-line" d="${p.line}"/>${med}</svg>
      <span class="corner">${esc(c.corner)}</span>
      <span class="beyond">${outsideNote(below, beyond, lo, hi, c.unit)}</span>
    </div>
    <div class="xt">${xTicks(ticks, lo, hi, c.unit)}</div></div>`;
}

/** The constructs the Overview draws: the CAP with the most reads, in listed order. */
function overviewConstructs(p: any): { keep: [string, any][]; more: number } {
  const all = Object.entries(p?.distributions?.constructs ?? {}) as [string, any][];
  if (all.length <= OVERVIEW_CAP) return { keep: all, more: 0 };
  const top = new Set([...all].sort((a, b) => (b[1].n_reads ?? 0) - (a[1].n_reads ?? 0))
    .slice(0, OVERVIEW_CAP).map(([k]) => k));
  return { keep: all.filter(([k]) => top.has(k)), more: all.length - OVERVIEW_CAP };
}

function yAxis(vals: number[], top: number, unit: string) {
  return vals.map((v, i) => `<span style="bottom:${(v / top * 100).toFixed(2)}%">${num(v)}${i === vals.length - 1 ? unit : ""}</span>`).join("");
}
const gridLines = (vals: number[], top: number) =>
  vals.map(v => `<span class="gl" style="bottom:${(v / top * 100).toFixed(2)}%"></span>`).join("");

/* Bottom row: title, and a headline value only once the run has one (2026-11-06:
   no sentence under the title, no count of constructs ready). */
function bottomCard(title: string, panel: string, head: string, headSub: string,
                    cols: string, names: string, ready: boolean, ys: string, grid: string,
                    wait: string) {
  return `<div ${cardOpen(panel, title)}>
    <div class="ovhead"><span class="t">${esc(title)}</span><span class="spacer"></span>
      <span class="hpair"><span class="hs">${esc(headSub)}</span><span class="h">${head}</span></span></div>
    <div class="cgrid"><div class="yt">${ready ? ys : ""}</div>
      <div class="cplot" role="img" aria-label="${esc(title)} by construct">
        ${grid}${cols}
        ${ready || !wait ? "" : `<span class="cwait">${esc(wait)}</span>`}
      </div><span></span><div class="names${ready ? "" : " wait"}">${names}</div></div></div>`;
}

/** One construct's card: its value, said (2026-11-13: one bar, one point and one
 *  box said less than their numbers). Until the construct has its reads, the wait. */
function tileCard(title: string, panel: string, value: string, sub: string, wait: string) {
  return `<div ${cardOpen(panel, title)}><div class="ovhead"><span class="t">${esc(title)}</span></div>
    <div class="tile">${value ? `<span class="tv">${value}</span><span class="ts">${esc(sub)}</span>`
      : wait ? `<span class="waittext">${esc(wait)}</span>` : ""}</div></div>`;
}

/** What the box over an empty Overview says: what it waits for while the run goes, and
 *  how far the run has got (run_status.json); why there is nothing once it has ended
 *  (Rust's note). */
function waitingFor(r: Poll): [string, string] {
  if (isActive(r)) {
    const st = r.status;
    if (!st) return ["Waiting for the first result", ""];
    if (st.inputs_total != null)
      return ["Waiting for the first result",
              `File ${Math.min(st.inputs_processed + 1, st.inputs_total)} of ${st.inputs_total}`];
    return ["Waiting for the first result", st.inputs_found
      ? `${num(st.inputs_found)} POD5 found so far` : "No POD5 from MinKNOW yet"];
  }
  const n = (r.note ?? "").trim();
  return [n ? n[0].toUpperCase() + n.slice(1) : "No result", ""];
}

function renderOverview(panel: any, r: Poll): string {
  /* Before the first result the Overview is drawn whole but empty, with one box over it
     saying what it waits for (2026-11-12); until then it was one line of text, and
     the cards appeared only with the first result. */
  const waiting = !panel;
  const p = panel ?? {};
  const shown = (s: string) => (waiting ? "" : s);
  const u = p.unassigned ?? {};
  const mapped: number | null = p.mapped_reads ?? null;
  const total: number | null = p.total_reads ?? null;
  const MIN = minReads(), PER = minPerConstruct();
  const enough = (mapped ?? 0) >= MIN;
  const mapPct = typeof p.mapping_rate === "number" ? p.mapping_rate * 100 : 0;
  // Each label on the same line as its number (2026-11-06).
  const reads = `<section class="ovcard reads" aria-label="Read count and mapping rate">
    <div class="nums">
      <div><span class="k">Reads</span><span class="v">${shown(tw("reads.total", total))}</span></div>
      <div><span class="k"><i class="key-m"></i>Mapped</span><span class="v">${shown(tw("reads.mapped", mapped))}</span><small>${
        shown(enough ? `✓ ≥ ${num(MIN)}` : `of ${num(MIN)} needed`)}</small></div>
      <div><span class="k"><i class="key-u"></i>Unmapped</span><span class="v">${shown(tw("reads.unmapped", u.unmapped))}</span></div>
      <div><span class="k">Mapping rate</span><span class="v">${shown(tw("reads.rate", p.mapping_rate, "p1"))}</span></div>
    </div>
    <div class="mapbar" role="img" aria-label="${num(mapped)} mapped of ${num(total)} reads">
      <span class="m" data-tw="reads.bar" data-tws="width" style="width:${mapPct.toFixed(1)}%"></span><span class="u"></span></div></section>`;

  // Top row: the whole run, from MIN mapped reads.
  const m = p.metrics ?? {};
  const fl = measured(m.full_length);
  const pa = measured(m.polya_tail);
  const qs = p.quality_score ?? {};
  const tops: TopCard[] = [
    { title: "Read length", panel: "read-length", head: twUnit("top.read-length.head", p.median_read_length, "nt"),
      headSub: "Median", h: p.distributions?.read_length, sqrt: true, unit: " nt", corner: "√ count",
      line: fl ? `Full length <b>${tw("top.read-length.fl.lo", fl.full_length_fraction_lower, "p1")}–${tw("top.read-length.fl.hi", fl.full_length_fraction_upper, "p1")}</b>` : "",
      warn: false, median: null, window: WINDOW_QUANTILES },
    { title: "Poly(A) tail", panel: "polya", head: pa ? twUnit("top.polya.head", pa.median_length, "nt") : NOT_MEASURED,
      headSub: "Median", h: p.distributions?.polya, sqrt: false, unit: " nt", corner: "",
      // Short enough for one line at the narrow window (2026-11-13: it wrapped).
      line: pa ? `IQR <b>${tw("top.polya.q1", pa.q1_length)}–${tw("top.polya.q3", pa.q3_length)} nt</b> · ${tw("top.polya.called", pa.pt_coverage, "p0")} tail called` : "",
      warn: pa?.correction_applied === false, median: pa?.median_length ?? null,
      /* Core withholds the whole run's correction: it is applied per construct, so the
         corrected values are in the Poly(A) and Constructs tables (2026-11-12). */
      warnWhy: pa?.correction_status === "withheld"
        ? "Uncorrected: the correction is set per construct. Each construct's corrected median is in the Poly(A) and Constructs tables"
        : "",
      window: WINDOW_QUANTILES },
    { title: "Read quality", panel: "quality", head: tw("top.quality.head", qs.mean, "n1"), headSub: "Mean Q",
      h: p.distributions?.quality_score, sqrt: false, unit: " Q", corner: "",
      line: `Median <b>${tw("top.quality.median", qs.median, "n1")}</b> · IQR ${tw("top.quality.q1", qs.Q1, "n1")}–${tw("top.quality.q3", qs.Q3, "n1")}`,
      warn: false, median: null, window: QUALITY_WINDOW },
  ];
  const topWait = waiting ? "" : readsWait(p);
  const top = `<div class="ovrow">${tops.map(c => topCardHtml(c, enough, topWait)).join("")}</div>`;

  // Bottom row: per construct, once any construct reaches PER reads.
  const { keep } = overviewConstructs(p);
  const okC = keep.filter(([, c]) => (c.n_reads ?? 0) >= PER);
  const ready = okC.length > 0;
  const names = keep.map(([k]) => `<span><b>${esc(k)}</b></span>`).join("");
  const hollow = (k: string, c: any) =>
    `<span class="col" title="${esc(k)}: ${num(c.n_reads)} reads, under ${num(PER)}"><span class="head hollow" style="bottom:0"></span></span>`;
  // Construct share (outlined bars, a gap between them): Core's share_of_reads, of
  // every read in the run, as the Constructs figure and its table give it.
  const shares = keep.map(([, c]) => typeof c.share_of_reads === "number" ? c.share_of_reads * 100 : 0);
  const shTop = Math.max(2, Math.ceil(Math.max(...shares, 0) / 2) * 2);
  const shareCols = keep.map(([k, c], i) =>
    `<span class="col" title="${esc(k)}: ${pct(c.share_of_reads)} of reads (${num(c.n_reads)})">${
      ready ? `<span class="cbar" data-tw="share.${esc(k)}" data-tws="height" style="height:${(shares[i] / shTop * 100).toFixed(2)}%"></span>` : ""}</span>`).join("");
  // Per-base identity: a filled head at the value from PER reads; below, a hollow head on
  // the baseline and no value.
  const idCols = keep.map(([k, c]) => {
    const v = measured(c.sequence_identity)?.consensus_identity_pct;
    if ((c.n_reads ?? 0) < PER || typeof v !== "number") return hollow(k, c);
    return `<span class="col" title="${esc(k)}: ${v.toFixed(1)}%, ${num(c.sequence_identity.mismatch_site_count)} flagged">
      <span class="stem" data-tw="identity.${esc(k)}.stem" data-tws="height" style="height:${v.toFixed(2)}%"></span><span class="head" data-tw="identity.${esc(k)}.head" data-tws="bottom" style="bottom:${v.toFixed(2)}%"></span></span>`;
  }).join("");
  // Full length: the low-high range is the head (low over all the construct's reads,
  // high over its admitted reads), both from Core.
  const flCols = keep.map(([k, c]) => {
    const f = measured(c.full_length);
    const lo = f?.full_length_fraction_lower, hi = f?.full_length_fraction;
    if ((c.n_reads ?? 0) < PER || typeof lo !== "number" || typeof hi !== "number") return hollow(k, c);
    return `<span class="col" title="${esc(k)}: ${pct(lo)}–${pct(hi)} full length">
      <span class="stem" data-tw="fl.${esc(k)}.stem" data-tws="height" style="height:${(lo * 100).toFixed(2)}%"></span>
      <span class="head range" data-tw="fl.${esc(k)}.head" data-tws="bottom,height" style="bottom:${(lo * 100).toFixed(2)}%; height:${((hi - lo) * 100).toFixed(2)}%"></span></span>`;
  }).join("");
  // Headline values only for a finished run whose constructs all reached PER reads.
  const finished = !isActive(r) && ready && okC.length === keep.length;
  const pooled = measured(m.sequence_identity);
  const idHead = finished && pooled ? [`${tw("bottom.identity.head", pooled.consensus_identity_pct, "n1")}%`, "Pooled"] : ["", ""];
  const flLo = okC.map(([, c]) => measured(c.full_length)?.full_length_fraction_lower).filter((v: any) => typeof v === "number");
  const flHi = okC.map(([, c]) => measured(c.full_length)?.full_length_fraction).filter((v: any) => typeof v === "number");
  const flHead = finished && flLo.length ? [`${tw("bottom.fl.lo", Math.min(...flLo), "p1")}–${tw("bottom.fl.hi", Math.max(...flHi), "p1")}`, "All constructs"] : ["", ""];
  /* Labels at 0, half and the top; a gridline at every quarter (2026-11-12: three
     faint lines were too few to read a value against). */
  const quarters = (top: number) => [0, 0.25, 0.5, 0.75, 1].map(f => f * top);
  const bottomWait = waiting ? "" : `Waiting for a construct to reach ${num(PER)} reads`;
  /* One construct: its three values, said. */
  const single = keep.length === 1;
  const [oneName, one] = single ? keep[0] : ["", {} as any];
  const oneOk = single && (one.n_reads ?? 0) >= PER;
  const oneWait = waiting ? "" : `Waiting for ${oneName} to reach ${num(PER)} reads`;
  const oneId = measured(one.sequence_identity);
  const oneFl = measured(one.full_length);
  const tiles = `<div class="ovrow">
    ${tileCard("Construct share", "constructs", oneOk ? tw("tile.share", one.share_of_reads, "p1") : "",
               `${oneName} · ${num(one.n_reads)} reads`, oneWait)}
    ${tileCard("Per-base identity", "identity",
               oneOk && typeof oneId?.consensus_identity_pct === "number" ? `${tw("tile.identity", oneId.consensus_identity_pct, "f1")}%` : "",
               `${oneName} · ${num(oneId?.mismatch_site_count)} flagged`, oneWait)}
    ${tileCard("Full length by construct", "full-length",
               oneOk && oneFl ? `${tw("tile.fl.lo", oneFl.full_length_fraction_lower, "p1")}–${tw("tile.fl.hi", oneFl.full_length_fraction, "p1")}` : "",
               `${oneName} · all reads to admitted reads`, oneWait)}</div>`;
  const bottom = single ? tiles : `<div class="ovrow">
    ${bottomCard("Construct share", "constructs", "", "", shareCols, names, ready,
                 yAxis([0, shTop / 2, shTop], shTop, "%"), gridLines(quarters(shTop), shTop), bottomWait)}
    ${bottomCard("Per-base identity", "identity", idHead[0], idHead[1], idCols, names, ready,
                 yAxis([0, 50, 100], 100, "%"), gridLines(quarters(100), 100), bottomWait)}
    ${bottomCard("Full length by construct", "full-length", flHead[0], flHead[1], flCols, names, ready,
                 yAxis([0, 50, 100], 100, "%"), gridLines(quarters(100), 100), bottomWait)}</div>`;

  // Sequencing information: one row, or what it needs.
  const inst = p.instrument ?? {};
  const health = waiting ? ""
    : instrumentAvailable(p)
    ? [inst.pore_activity?.available ? `Pore occupancy <b>${tw("health.pore", inst.pore_activity.strand_fraction, "p1")}</b>` : "",
       inst.end_reason?.available ? `Ejected <b>${tw("health.ejected", inst.end_reason.ejected_fraction, "p2")}</b>` : "",
       inst.voltage?.available ? `Voltage <b>${twUnit("health.voltage", inst.voltage.latest_running_mv, "mV")}</b>` : ""]
      .filter(Boolean).join(" · ")
    : `Pore occupancy · End reason · Voltage: ${NEEDS_MINKNOW[0].toLowerCase()}${NEEDS_MINKNOW.slice(1)}`;
  const healthRow = `<div ${cardOpen("flow-cell", "Sequencing information").replace('class="ovcard"', 'class="ovcard health"')}>
    <span class="t">Sequencing information</span>
    <span class="hv">${health}</span><span class="spacer"></span><span class="hv" aria-hidden="true">›</span></div>`;

  const [wait, waitSub] = waitingFor(r);
  const overlay = waiting
    ? `<div class="ovwait" role="status"><b>${esc(wait)}</b>${
        waitSub ? `<small>${esc(waitSub)}</small>` : ""}</div>` : "";
  return `<div class="ov">${reads}${top}${bottom}${healthRow}${overlay}</div>`;
}

function wireOverview() {
  for (const b of Array.from($("panels").querySelectorAll<HTMLElement>("[data-open]"))) {
    b.addEventListener("click", () => selectPanel(b.dataset.open!));
    b.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectPanel(b.dataset.open!); }
    });
  }
}

// --- panels 2-8 --------------------------------------------------------------------------
/** Core's caption behind an ⓘ: the panel shows the title only, the report every word. */
const infoTip = (caption: string) => caption
  ? `<details class="info"><summary aria-label="About this figure">ⓘ</summary><p>${esc(caption)}</p></details>` : "";

/** A figure card. Plotly's own title is drawn here as page text instead: the construct
 *  name and Core's one-line detail, at the page's sizes, under the figure's name. Inside
 *  the chart it took a two-line top margin and its lines collided with the legend. */
function figureCard(f: Figure, i: number, tall = false, switcher = "") {
  return `<div class="chartcard" data-card="${i}"><div class="charthead"><span class="ct">${esc(f.title)}</span>${infoTip(f.caption)}
      <span class="spacer"></span>${switcher}<span class="modes" data-modes="${i}"></span></div>
    <div class="chartsub" data-sub="${i}"></div>
    <div class="fig${tall ? " tall" : ""}" id="gui-plot-${i}" data-fig="${i}"></div></div>`;
}

/** Core's title text, split as Core writes it: "name<br><span …>detail</span>". Text only:
 *  the markup is Plotly's, and the page sets its own type. */
function titleParts(text: string): { name: string; detail: string } {
  const strip = (s: string) => s.replace(/<[^>]*>/g, "").trim();
  const parts = String(text ?? "").split(/<br\s*\/?>/i);
  return parts.length > 1 ? { name: strip(parts[0]), detail: strip(parts.slice(1).join(" ")) }
                          : { name: "", detail: strip(parts[0]) };
}

function setSub(i: number, titleText: string) {
  const el = document.querySelector<HTMLElement>(`[data-sub="${i}"]`);
  if (!el) return;
  const { name, detail } = titleParts(titleText);
  el.innerHTML = (name ? `<span class="cn">${esc(name)}</span>` : "") +
    (detail ? `<span class="cd">${esc(detail)}</span>` : "");
}

const constructNames = () => new Set(Object.keys(lastPoll?.panel?.distributions?.constructs ?? {}));
const isConstructMenu = (m: any, names: Set<string>) =>
  (m.buttons ?? []).some((b: any) => names.has(b.label));

/* Per drawn chart: the construct visibility Core's button set, the view chosen in the
   figure's other control (Both / Corrected / Uncorrected), and the series the legend hid,
   combined on every change. */
type FigState = { base: boolean[]; construct: boolean[] | null; mode: any | null; hidden: Set<string> };
/** A trace's legend entry: its group (a series' bars and curve share one), else its name. */
const legendKey = (t: any) => String(t?.legendgroup ?? t?.name ?? "");
const figState = new WeakMap<HTMLElement, FigState>();

/** Visibility = the construct's traces AND the chosen view's traces. Both arrays are
 *  Core's (its two controls' button args); the shell only combines them, so the two
 *  controls no longer undo each other. */
function applyVisibility(el: HTMLElement) {
  const st = figState.get(el);
  if (!st) return;
  const show = st.base.map((b, i) => (st.construct ? st.construct[i] : b) &&
    (st.mode?.opacity ? Number(st.mode.opacity[i]) > 0 : true));
  // A series the legend hid stays in the legend, greyed, so a second click brings it back.
  const data: any[] = (el as any).data ?? [];
  const upd: any = { visible: show.map((v, i) => !v ? false
    : st.hidden.has(legendKey(data[i])) ? "legendonly" : true) };
  if (st.mode?.hoverinfo) upd.hoverinfo = st.mode.hoverinfo;
  if (st.mode?.showlegend) upd.showlegend = st.mode.showlegend;
  Plotly.restyle(el, upd);
}

/** Draw figure `i` into `el`, dark, set to construct `row` when Core's figure has a
 *  construct control ("" = the figure's own default). Core's controls are hidden: the
 *  construct is chosen from the table, the other controls are drawn in the card header. */
function drawFig(el: HTMLElement, i: number, row: string) {
  const f = figures[i];
  if (!f) return;
  const s = themeSpec(f.spec);
  const menus: any[] = s.layout.updatemenus ?? [];
  const names = constructNames();
  const ci = menus.findIndex(m => isConstructMenu(m, names));
  // With no table on the panel (Read length), Core's own construct dropdown stays.
  const drive = el.dataset.drive === "1";
  s.layout.updatemenus = menus.map((m, k) =>
    (k === ci && drive) || (k !== ci && m.type === "buttons") ? { ...m, visible: false } : m);
  // Room at the top only for a control of Core's that is still shown.
  const shownMenu = s.layout.updatemenus.some((m: any) => m.visible !== false);
  s.layout.margin = { ...(s.layout.margin ?? {}), t: shownMenu ? 64 : 28 };
  let titleText: string = s.layout.title?.text ?? "";
  s.layout.title = { text: "" };
  const btn = drive && ci >= 0 && row ? (menus[ci].buttons ?? []).find((b: any) => b.label === row) : null;
  // Core draws per-construct charts only for constructs with min_reads_per_construct
  // reads; for the rest the chart says so instead of keeping the last construct's.
  if (drive && ci >= 0 && row && !btn) { noFigure(el, i, row); return; }
  Plotly.newPlot(el, s.data, s.layout, s.config);
  figState.set(el, { base: s.data.map((t: any) => t.visible !== false), construct: null, mode: null,
                     hidden: new Set() });
  /* A legend entry shows or hides its series (2026-11-13). Handled here, not by
     Plotly: a series is one legend group across every construct's traces, and Plotly's
     own toggle would bring back the constructs the table did not choose. */
  (el as any).on("plotly_legendclick", (ev: any) => {
    const st = figState.get(el);
    if (!st) return false;
    const k = legendKey(ev.data?.[ev.curveNumber]);
    if (st.hidden.has(k)) st.hidden.delete(k); else st.hidden.add(k);
    applyVisibility(el);
    return false;
  });
  (el as any).on("plotly_legenddoubleclick", () => false);
  if (btn) {
    const [traces, lay] = btn.args ?? [];
    if (lay?.title?.text) titleText = lay.title.text;
    const rest = { ...(lay ?? {}) };
    delete rest.title;
    if (Object.keys(rest).length) Plotly.relayout(el, rest);
    figState.get(el)!.construct = traces?.visible ?? null;
    applyVisibility(el);
  }
  setSub(i, titleText);
  // The figure's other controls (Both / Corrected / Uncorrected), as page buttons.
  const slot = document.querySelector<HTMLElement>(`[data-modes="${i}"]`);
  if (!slot) return;
  const modeMenus = menus.filter((m, k) => k !== ci && m.type === "buttons");
  slot.innerHTML = modeMenus.map((m, mi) => (m.buttons ?? []).map((b: any, bi: number) =>
    `<button class="seg" data-m="${mi}" data-b="${bi}" aria-pressed="${bi === (m.active ?? 0)}">${esc(b.label)}</button>`).join("")).join("");
  for (const b of Array.from(slot.querySelectorAll<HTMLButtonElement>("button.seg"))) {
    b.addEventListener("click", () => {
      const btnArgs = modeMenus[Number(b.dataset.m)].buttons[Number(b.dataset.b)].args ?? [];
      figState.get(el)!.mode = btnArgs[0] ?? null;
      // A view is a preset: it shows its series whatever the legend hid.
      figState.get(el)!.hidden.clear();
      applyVisibility(el);
      for (const o of Array.from(slot.querySelectorAll<HTMLButtonElement>(`button.seg[data-m="${b.dataset.m}"]`)))
        o.setAttribute("aria-pressed", String(o === b));
    });
  }
}

/** In place of a per-construct chart Core did not draw for `row`. */
function noFigure(el: HTMLElement, i: number, row: string) {
  const n = lastPoll?.panel?.distributions?.constructs?.[row]?.n_reads;
  el.classList.add("nofig");
  el.innerHTML = `<p class="empty">${esc(row)} has ${num(n)} reads, under the ${
    num(minPerConstruct())} a construct needs for its own charts.</p>`;
  setSub(i, "");
  const slot = document.querySelector<HTMLElement>(`[data-modes="${i}"]`);
  if (slot) slot.innerHTML = "";
}

/** Draw every `.fig[data-fig]` under `root`, each set to `row`. */
function drawFigures(root: HTMLElement, row = "") {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>(".fig[data-fig]")))
    drawFig(el, Number(el.dataset.fig), row);
}

/** The figures on one panel, as cards, in Core's order unless `order` names one. `fit`
 *  lays them side by side in one window (2026-11-13). */
function panelFigures(key: string, skip: Set<number> = new Set(), order: string[] = [], drive = false,
                      fit = false) {
  const on = figures.map((f, i) => ({ f, i })).filter(({ f, i }) => f.panel === key && !skip.has(i));
  const rank = (id: string) => { const k = order.indexOf(id); return k < 0 ? order.length : k; };
  on.sort((a, b) => rank(a.f.id) - rank(b.f.id));
  const cards = on.map(({ f, i }) => figureCard(f, i).replace('data-fig=', `data-drive="${drive ? 1 : 0}" data-fig=`)).join("");
  return cards ? `<div class="charts${fit ? " fit" : ""}"${fit ? ` style="--cols:${on.length}"` : ""}>${cards}</div>` : "";
}

/* With one construct, a by-construct figure is one bar (2026-11-13): left out, and
   the identity panel opens on the construct's own per-position figure. */
const BY_CONSTRUCT = new Set(["median-length", "identity-by-construct", "share"]);
const oneConstruct = (p: any) => Object.keys(p?.distributions?.constructs ?? {}).length === 1;
/** The figures a panel leaves out for this run: the by-construct ones, with one construct. */
function singleSkip(p: any): Set<number> {
  if (!oneConstruct(p)) return new Set();
  return new Set(figures.map((f, i) => (BY_CONSTRUCT.has(f.id) ? i : -1)).filter(i => i >= 0));
}

/** Whole-run histogram, drawn live from the snapshot (before figures.json exists). */
function hist(h: any, unit: string, q: [number, number] = WINDOW_QUANTILES) {
  const c: number[] = h?.bin_counts ?? [];
  if (!c.reduce((a: number, b: number) => a + b, 0)) return `<div class="inst-none">no reads with a value yet</div>`;
  const { shown, lo, hi } = drawnBins(h, q);
  const p = histPath(shown, true);
  return `<div class="chartcard"><svg class="hist" viewBox="0 0 300 100" preserveAspectRatio="none" role="img" aria-label="histogram">
    <path class="hx-area" d="${p.area}"/><path class="hx-line" d="${p.line}"/></svg>
    <div class="hist-foot"><span>${num(lo)}</span><span>${esc(unit)} · √ scale</span><span>${num(hi)}</span></div></div>`;
}

/** One line on short poly(A) tails; empty unless a cutoff is configured. */
function shortTails(d: any) {
  const run = d?.polya_tail;
  if (run?.short_tail_status === "measured") {
    const ruler = run.short_tail_judged_on === "corrected" ? "corrected" : "uncorrected";
    return `<p class="dim">Short tails (under ${num(run.short_tail_threshold)} nt, ` +
      `${ruler}): <b>${pct(run.short_tail_ratio)}</b> (${pct(run.short_tail_ratio_lower)}` +
      `–${pct(run.short_tail_ratio_upper)})</p>`;
  }
  const per = Object.entries(d?.constructs ?? {})
    .filter(([, c]) => (c as any)?.polya_tail?.short_tail_status === "measured")
    .map(([k, c]) => `${esc(k)} <b>${pct((c as any).polya_tail.short_tail_ratio)}</b>`);
  return per.length ? `<p class="dim">Short tails: ${per.join(" · ")}</p>` : "";
}

type Col = { head: string; n?: boolean; cell: (c: any, whole: boolean) => string };

/* The table columns per panel: only what Core already reports per construct. */
function tableCols(key: string, p: any): Col[] {
  const reads: Col = { head: "Reads", n: true, cell: (c, w) => num(w ? p.total_reads : c.n_reads) };
  if (key === "polya") {
    const scale = polyaScale(p);
    return [reads,
      { head: "Median", n: true, cell: c => polyaMedian(measured(c.polya_tail)) },
      { head: "IQR", n: true, cell: c => { const t = shownTail(measured(c.polya_tail));
          return t ? `${num(t.q1)}–${num(t.q3)}` : NOT_MEASURED; } },
      { head: "Uncorrected", n: true, cell: c => withUnit(measured(c.polya_tail)?.median_length, "nt") },
      { head: "", cell: c => iqrBar(shownTail(measured(c.polya_tail)), scale) }];
  }
  if (key === "identity")
    return [reads,
      { head: "Per-base identity", n: true, cell: c => { const v = measured(c.sequence_identity)?.consensus_identity_pct;
          return typeof v === "number" ? `${v.toFixed(1)}%` : NOT_MEASURED; } },
      { head: "Flagged", n: true, cell: c => num(measured(c.sequence_identity)?.mismatch_site_count) }];
  // full-length
  return [reads, { head: "Full length", n: true, cell: (c, w) => {
    const f = measured(w ? c : c.full_length);
    const lo = f?.full_length_fraction_lower;
    const hi = w ? f?.full_length_fraction_upper : f?.full_length_fraction;
    return f ? `${pct(lo)}–${pct(hi)}` : NOT_MEASURED; } }];
}

/** The poly(A) values a table row reports (2026-11-12): the corrected median and
 *  IQR where the construct's correction applied, else the uncorrected ones, flagged. The
 *  whole run's row is always uncorrected: Core withholds a whole-run correction. */
function shownTail(t: any): { median: any; q1: any; q3: any; corrected: boolean } | null {
  if (!t) return null;
  return t.correction_applied === true && typeof t.median_length_corrected === "number"
    ? { median: t.median_length_corrected, q1: t.q1_length_corrected, q3: t.q3_length_corrected, corrected: true }
    : { median: t.median_length, q1: t.q1_length, q3: t.q3_length, corrected: false };
}

/** A reported median: an uncorrected one is said so, with why, so it is never read as
 *  corrected. */
function polyaMedian(t: any): string {
  const s = shownTail(t);
  if (!s) return NOT_MEASURED;
  if (s.corrected) return withUnit(s.median, "nt");
  const why = t.correction_status === "withheld"
    ? "the correction is set per construct, and withheld for the whole run" : "no correction applied";
  return `${withUnit(s.median, "nt")} <span class="uncorr" title="Uncorrected: ${esc(why)}">⚠</span>`;
}

/** Under the poly(A) table, why the whole run's row is uncorrected beside a chart that
 *  draws a corrected curve (2026-11-13: the curve stays; the page says why). */
function wholeRunNote(key: string, p: any): string {
  const withheld = key === "polya" && measured(p?.metrics?.polya_tail)?.correction_status === "withheld";
  if (!withheld) return "";
  return `<p class="tablenote">⚠ Whole run: uncorrected, as the correction is set per construct. ` +
    `In the whole-run chart, the chart's corrected curve pools the reads assigned to a construct; ` +
    `the uncorrected curve holds every read with a tail.</p>`;
}

/* One axis shared by every row's Q1-median-Q3 bar, so rows compare at a glance. It spans
   the rows' own range: from zero, every bar sat in the last fifth of the cell (Chromium
   check, 2026-11-12). */
function polyaScale(p: any): [number, number] {
  const all = [p.metrics?.polya_tail, ...Object.values(p.distributions?.constructs ?? {}).map((c: any) => c.polya_tail)]
    .map(measured).map(shownTail).filter(Boolean) as any[];
  const lo = Math.min(...all.map(t => t.q1 ?? Infinity)), hi = Math.max(...all.map(t => t.q3 ?? -Infinity));
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  const pad = Math.max((hi - lo) * 0.05, 1);
  return [lo - pad, hi + pad];
}
function iqrBar(t: { median: any; q1: any; q3: any } | null, [lo, hi]: [number, number]) {
  if (!t || t.q1 == null || t.q3 == null) return "";
  const x = (v: number) => ((v - lo) / (hi - lo) * 100).toFixed(1);
  return `<span class="iqr"><i style="left:${x(t.q1)}%; width:${(+x(t.q3) - +x(t.q1)).toFixed(1)}%"></i>` +
    `<b style="left:${x(t.median ?? t.q1)}%"></b></span>`;
}

/* The panel's whole-run row reads the run-level entry of the same metric. */
function wholeRunEntry(key: string, p: any) {
  const m = p.metrics ?? {};
  if (key === "polya") return { polya_tail: m.polya_tail };
  if (key === "identity") return { sequence_identity: m.sequence_identity };
  if (key === "full-length") return m.full_length;
  return {};
}

/* Per table panel: the figure beside the table for the Whole run row (`whole`) and for a
   construct row (`per`); `alts` share the `per` figure's card, switched by buttons in its
   header. Every per-construct figure on the panel follows the table's row. Full length
   had its read start and read end drawn below the card, which ran the panel past one
   window (2026-11-12). */
const PANEL_FIGS: Record<string, { whole?: string; per?: string; alts?: string[] }> = {
  "polya": { whole: "polya", per: "polya-by-construct" },
  "identity": { whole: "identity-by-construct", per: "per-position" },
  "full-length": { per: "shortfall", alts: ["read-start", "read-end"] },
};
/* The switch's short names; the card's title stays Core's. */
const SWITCH_NAMES: Record<string, string> = {
  "shortfall": "Shortfall", "read-start": "Read start", "read-end": "Read end",
};
let chosenRow: Record<string, string> = {};
/* The figure switched to, per panel with a switch. */
let chosenFig: Record<string, string> = {};

/** The buttons that switch a card between figures; none when fewer than two exist. */
function figSwitch(ids: string[], on: string): string {
  const have = ids.filter(id => figures.some(f => f.id === id));
  if (have.length < 2) return "";
  return `<span class="figswitch" role="group" aria-label="Figure">${have.map(id =>
    `<button class="seg" data-figswitch="${id}" aria-pressed="${id === on}">${esc(SWITCH_NAMES[id] ?? id)}</button>`).join("")}</span>`;
}

/** A table panel's figures for this run: PANEL_FIGS, less a by-construct whole-run
 *  figure when there is one construct (identity then opens on per-position). */
function panelFigsOf(key: string, p: any): { whole?: string; per?: string; alts?: string[] } {
  const pf = { ...(PANEL_FIGS[key] ?? {}) };
  if (oneConstruct(p) && pf.whole && BY_CONSTRUCT.has(pf.whole)) delete pf.whole;
  return pf;
}

/** The row a panel opens on: Whole run when it has a whole-run figure, else the first
 *  construct, so a per-construct chart never opens on a construct nobody chose. */
function rowFor(key: string, p: any): string {
  if (key in chosenRow) return chosenRow[key];
  return panelFigsOf(key, p).whole ? "" : (Object.keys(p?.distributions?.constructs ?? {})[0] ?? "");
}

function renderTablePanel(key: string, p: any): string {
  const cols = tableCols(key, p);
  const constructs = Object.entries(p?.distributions?.constructs ?? {}) as [string, any][];
  const sel = rowFor(key, p);
  const pf = panelFigsOf(key, p);
  const row = (name: string, label: string, c: any, whole: boolean) =>
    `<tr class="pick${whole ? " dimrow" : ""}" data-row="${esc(name)}" aria-selected="${sel === name}">
      <td>${esc(label)}</td>${cols.map(k => `<td${k.n ? ' class="n"' : ""}>${k.cell(c, whole)}</td>`).join("")}</tr>`;
  const wholeRow = pf.whole || !figures.length ? row("", "Whole run", wholeRunEntry(key, p), true) : "";
  const table = `<div class="tablecol"><div class="tablebox"><table><thead><tr><th>Construct</th>${
    cols.map(k => `<th${k.n ? ' class="n"' : ""}>${esc(k.head)}</th>`).join("")}</tr></thead><tbody>
    ${wholeRow}${constructs.map(([k, c]) => row(k, k, c, false)).join("")}</tbody></table></div>${
    wholeRunNote(key, p)}</div>`;
  if (!figures.length) {
    const live = key === "polya" ? hist(p.distributions?.polya, "nt") + shortTails(p.distributions)
      : `<p class="empty">${esc(noChartsYet())}</p>`;
    return `<div class="split ${key}">${table}<div>${live}</div></div>`;
  }
  const idx = (id?: string) => figures.findIndex(f => f.id === id);
  const choices = pf.per ? [pf.per, ...(pf.alts ?? [])] : [];
  const perId = choices.includes(chosenFig[key]) ? chosenFig[key] : pf.per;
  const main = !sel && pf.whole ? idx(pf.whole) : idx(perId);
  // Every figure the card can show is kept out of the cards below it, and with one
  // construct its by-construct figures too.
  const skip = new Set([...[pf.whole, ...choices].map(idx).filter(i => i >= 0), ...singleSkip(p)]);
  const switcher = main === idx(perId) ? figSwitch(choices, perId ?? "") : "";
  const mainCard = main >= 0
    ? figureCard(figures[main], main, true, switcher).replace('data-fig=', 'data-drive="1" data-fig=')
    : `<p class="empty">${esc(noChartsYet())}</p>`;
  return `<div class="split ${key}">${table}<div class="mainfig">${mainCard}${
    key === "polya" ? shortTails(p.distributions) : ""}</div></div>${panelFigures(key, skip, [], true)}`;
}

function wireTablePanel(key: string, p: any) {
  const el = $("panels");
  for (const tr of Array.from(el.querySelectorAll<HTMLElement>("tr.pick"))) {
    tr.addEventListener("click", () => { chosenRow[key] = tr.dataset.row ?? ""; renderCurrentPanel(); });
  }
  for (const b of Array.from(el.querySelectorAll<HTMLButtonElement>("button[data-figswitch]"))) {
    b.addEventListener("click", () => { chosenFig[key] = b.dataset.figswitch!; renderCurrentPanel(); });
  }
  if (figures.length) drawFigures(el, rowFor(key, p));
}

function renderQuality(p: any) {
  const qs = p?.quality_score ?? {};
  const stat = (k: string, v: string) => `<span class="qstat"><span>${k}</span><b>${v}</b></span>`;
  const head = `<div class="chartcard"><div class="charthead"><span class="ct">Mean read Q</span></div>
    <div class="qstats">${stat("Mean", num(qs.mean, 1))}${stat("Median", num(qs.median, 1))}${
      stat("IQR", `${num(qs.Q1, 1)}–${num(qs.Q3, 1)}`)}</div></div>`;
  return head + (figures.length ? panelFigures("quality") : hist(p?.distributions?.quality_score, "Q", QUALITY_WINDOW));
}

function renderConstructs(p: any) {
  const by = Object.entries(p?.distributions?.constructs ?? {}) as [string, any][];
  const u = p?.unassigned ?? {};
  /* Every share is Core's share_of_reads, of every read in the run, so the reads that
     belong to no construct are the last row, as they are a bar in the figure above, and
     the column adds up. Among them, mapped reads on a contig the reference lacks are
     counted in the row's name. */
  const off: number = u.off_reference ?? 0;
  const rows = by.map(([k, c]) => `<tr><td>${esc(k)}</td><td class="n">${num(c.n_reads)}</td>
      <td class="n">${pct(c.share_of_reads)}</td>
      <td class="n">${(() => { const v = measured(c.sequence_identity)?.consensus_identity_pct; return typeof v === "number" ? v.toFixed(1) + "%" : NOT_MEASURED; })()}</td>
      <td class="n">${polyaMedian(measured(c.polya_tail))}</td>
      <td class="n">${(() => { const f = measured(c.full_length); return f ? `${pct(f.full_length_fraction_lower)}–${pct(f.full_length_fraction)}` : NOT_MEASURED; })()}</td>
      <td class="n">${pct(c.assignment?.mapq0_fraction)}</td></tr>`).join("")
    + ((u.n_reads ?? 0) > 0 ? `<tr class="dimrow"><td>(unassigned)${off > 0 ? ` · ${num(off)} off reference` : ""}</td>
      <td class="n">${num(u.n_reads)}</td>
      <td class="n">${pct(u.share_of_reads)}</td><td></td><td></td><td></td><td></td></tr>` : "");
  const table = `<div class="tablebox"><table><thead><tr><th>Construct</th><th class="n">Reads</th>
    <th class="n">Share of reads</th><th class="n">Per-base identity</th><th class="n">Poly(A) median</th>
    <th class="n">Full length</th><th class="n">MAPQ 0</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  return (figures.length ? panelFigures("constructs", singleSkip(p)) : "") + table;
}

/** The channel states drawn as a band of their own, in the report's stacking order
 *  (figspec `_PORE_BANDS`, held equal by test_gui_state_bar.py). Every other state folds
 *  into "other", as in the report's occupancy figure. */
const PORE_BANDS = ["strand", "pore", "adapter", "unavailable"];

/** Stacked bar of the run-aggregate channel states, in the report's bands and colours. */
function stateBar(states: Record<string, any>) {
  const share = (k: string) => states[k].fraction as number;
  const rest = Object.keys(states).filter(k => !PORE_BANDS.includes(k));
  const shown = PORE_BANDS.filter(k => k in states)
    .map((k): [string, string, number] => [`st-${k}`, k, share(k)]);
  if (rest.length)
    shown.push(["st-other", `other (${rest.length} states)`, rest.reduce((s, k) => s + share(k), 0)]);
  return `<div class="sbar">${shown.map(([c, k, f]) =>
      `<span class="${c}" style="width:${(f * 100).toFixed(2)}%" title="${esc(k)} ${pct(f, 2)}"></span>`).join("")}</div>
    <div class="key">${shown.map(([c, k, f]) =>
      `<span><i class="${c}"></i>${esc(k)} ${pct(f)}</span>`).join("")}</div>`;
}

/** A module that could not be read says why, instead of drawing an empty chart. */
function instNone(title: string, m: any) {
  const why = m?.unavailable_reason ?? "not reported by this run";
  const stale = m?.stale_reason ? ` (showing the last good snapshot: ${esc(m.stale_reason)})` : "";
  return `<div class="inst-block"><div class="inst-title">${esc(title)}</div>
    <div class="inst-none">${esc(why)}${stale}</div></div>`;
}

/* The flow cell in one window (2026-11-13: six panels ran past it): each module's
   numbers over Core's figure of the same module, in three columns. The strand sparkline
   and the voltage one are gone: Core's occupancy and voltage figures under them draw the
   same series, with axes. */
const FLOW_ORDER = ["pore", "end-reason", "voltage"];

function renderFlowCell(p: any) {
  const inst = p?.instrument;
  if (!inst) return panelFigures("flow-cell", new Set(), FLOW_ORDER, false, true);
  const pa = inst.pore_activity, er = inst.end_reason, vo = inst.voltage;
  let a: string;
  if (!pa?.available) a = instNone("Pore occupancy", pa);
  else {
    const ser = pa.series ?? {};
    const cov: (number | null)[] = ser.coverage ?? [];
    // A bin short of a full bin's channel-time is NOISIER, not lower; Core's figure marks it.
    const nPart = cov.filter(c => c !== null && c < 0.99).length;
    a = `<div class="inst-block">
      <div class="inst-title">Pore occupancy${big(pct(pa.strand_fraction), "sequencing")}</div>
      ${stateBar(pa.states ?? {})}
      <div class="inst-note">over ${esc(pa.n_minutes)} min, in ${esc(ser.bin_minutes)} min bins${
        nPart ? ` · ${nPart} partial bin(s)` : ""}</div></div>`;
  }
  let b: string;
  if (!er?.available) b = instNone("Read end reason", er);
  else {
    const rows = Object.entries(er.reasons ?? {}).sort((x: any, y: any) => y[1].n - x[1].n)
      .map(([k, v]: any) => `<tr><td>${esc(k)}</td><td class="n">${v.n.toLocaleString()}</td>
        <td class="n">${pct(v.fraction, 2)}</td></tr>`).join("");
    b = `<div class="inst-block">
      <div class="inst-title">Read end reason${big(pct(er.ejected_fraction, 2), "ejected")}</div>
      <table><tbody>${rows}</tbody></table>
      <div class="inst-note">${num(er.n_reads)} reads</div></div>`;
  }
  let c: string;
  if (!vo?.available) c = instNone("Bias voltage", vo);
  else {
    const bad: number[] = vo.inconsistent_scans ?? [];
    c = `<div class="inst-block">
      <div class="inst-title">Bias voltage${big(withUnit(vo.latest_running_mv, "mV"), "now")}</div>
      <div class="inst-note">${esc(vo.first_running_mv)} → ${esc(vo.latest_running_mv)} mV over
        ${esc(vo.n_scans)} mux scan(s)
        ${bad.length ? `<span class="warn">⚠ scan(s) ${bad.join(", ")} reported more than one
          voltage; the plotted value is one of several.</span>` : ""}</div></div>`;
  }
  return `<div class="inst">${a}${b}${c}</div>${panelFigures("flow-cell", new Set(), FLOW_ORDER, false, true)}`;
}

// --- polling -----------------------------------------------------------------------------

function clearRun() {
  resetTweens();
  figures = [];
  chosenRow = {};
  chosenFig = {};
  current = "overview";
  lastPoll = null;
}

async function tick() {
  let r: Poll;
  try { r = await invoke<Poll>("poll_run"); }
  catch (e) { $("log").textContent = "poll failed: " + e; return; }
  const was = lastPoll;
  lastPoll = r;
  $("log").textContent = r.log_tail;
  const active = isActive(r);
  ($("setupfields") as HTMLFieldSetElement).disabled = active;
  for (const k of Array.from(document.querySelectorAll<HTMLButtonElement>(".kind"))) k.disabled = active;
  syncFlow();
  if (r.state === "idle") return;
  renderRunCard(r);
  // Core rewrites figures.json on every render, interims included: read it when it moves.
  const loaded = r.figures_changed ? await loadFigures() : false;
  /* Repaint the panel only when the snapshot moved, the run changed state, or the
     figures arrived: a redraw on every 1 s tick would reset the charts' zoom. */
  if (page === "run" && (r.changed || loaded || !was || was.state !== r.state)) renderCurrentPanel();
  // "stopping" is still an ACTIVE run; the timer stops only when the run has ended.
  if (!active && timer !== undefined) {
    clearInterval(timer);
    timer = undefined;
    /* The folder now exists: the same name would continue this run, so say it. */
    void checkRunDir();
  }
}

async function loadFigures(): Promise<boolean> {
  let f: Figure[] | null;
  try { f = await invoke<Figure[] | null>("read_figures"); }
  catch (e) { $("log").textContent += `\ncould not read figures: ${e}`; return false; }
  if (!f || !f.length) return false;
  figures = f;
  return true;
}

// =====================================================================================
// Opening page: Settings › Device on first launch (unless skipped), else Start. A run
// cannot outlive the app, so there is never one going at launch.
// =====================================================================================
applyTheme(themeName, false);
loadMachine().then(async first => {
  let skipped = false;
  try { skipped = localStorage.getItem(SKIP_KEY) === "1"; } catch { /* treated as not skipped */ }
  showPage(first && !skipped ? "device" : "start");
  await refreshConfig();
});
showPage("start");
void tick();
