// mRNAspect desktop shell — the Rust half.
//
// Everything below except the #[tauri::command] attributes and run() was typechecked and
// DRIVEN AGAINST REAL CORE on Linux (Tauri cannot build on that host: no webkit2gtk, no
// root), covering: a normal run, a bad reference that never produces a snapshot, a
// double click, the mtime gate, and cancellation of a 60-input run.
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
#[cfg(unix)]
use std::os::unix::process::CommandExt;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::Manager;

struct RunState {
    child: Child,
    run_dir: PathBuf,
    stdout_path: PathBuf,
    stderr_path: PathBuf,
    /// latest_snapshot.json's and figures.json's mtimes, in nanoseconds: an input in bam
    /// mode takes under a second, so a whole-second stamp missed a rewrite.
    last_mtime: Option<u128>,
    figs_mtime: Option<u128>,
    last_panel: Option<Value>,
    /// run_status.json (Core's runtime/run_status.py): inputs found, done and in progress,
    /// written from the run's first moment, so the run card has a count before the first
    /// input finishes (GUI round 2, 2026-11-12). The last one read, and its mtime.
    last_status: Option<Value>,
    status_mtime: Option<u128>,
    result: Option<Value>,
    exit_code: Option<i32>,
    /// When the operator first asked to stop, in unix seconds; None if they have not.
    ///
    /// Core's abort is GRACEFUL BY DESIGN: the signal sets a flag that `main.py`'s loop
    /// checks BETWEEN inputs, so the in-flight one finishes, and the basecall child runs
    /// in its own session (`basecaller.py`) precisely so a group signal does not kill it
    /// mid-read. In bam mode an input is sub-second and the stop looks instant; a POD5
    /// basecall is minutes, and the GUI used to repaint "running" on the very next poll —
    /// erasing its own "stopping…" message and making a working stop look like a dead
    /// button. So the request has to be REMEMBERED here and reported, not just sent.
    stop_requested_at: Option<u64>,
    /// When this app spawned Core, in unix seconds: the run card's Elapsed counts from
    /// here. Core keeps per-input processing time, not wall-clock time since launch.
    started_at: u64,
    /// When this app first saw Core exit, in unix seconds; None while it runs.
    finished_at: Option<u64>,
}

static RUN: Mutex<Option<RunState>> = Mutex::new(None);

#[derive(Serialize)]
pub struct Poll {
    state: &'static str,
    changed: bool,
    /// figures.json was rewritten since the last poll (every render writes it, interims
    /// included); the GUI then reads it with `read_figures`.
    figures_changed: bool,
    age_s: Option<u64>,
    panel: Option<Value>,
    result: Option<Value>,
    exit_code: Option<i32>,
    /// Seconds since the operator first pressed stop, while it is still taking effect.
    stopping_for_s: Option<u64>,
    /// Seconds since Core was spawned; frozen at the exit once the run has finished.
    elapsed_s: Option<u64>,
    /// When Core exited, in unix seconds (the TypeScript side formats it).
    finished_at: Option<u64>,
    log_tail: String,
    note: Option<String>,
    /// The run's progress from run_status.json, or None before Core has written it.
    status: Option<Value>,
}

fn now_s() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn mtime_s(p: &PathBuf) -> Option<u64> {
    let m = std::fs::metadata(p).ok()?;
    m.modified().ok()?.duration_since(UNIX_EPOCH).ok().map(|d| d.as_secs())
}

fn mtime_ns(p: &PathBuf) -> Option<u128> {
    let m = std::fs::metadata(p).ok()?;
    m.modified().ok()?.duration_since(UNIX_EPOCH).ok().map(|d| d.as_nanos())
}

fn extract_panel(v: &Value) -> Value {
    let s = &v["qc_metrics"]["summary"];
    let u = &v["qc_metrics"]["unassigned"];
    json!({
        "last_input":        v["last_input"],
        "total_reads":       v["total_reads"],
        "mapped_reads":      s["mapped_reads"],
        "mapping_rate":      s["mapping_rate"],
        // Both are passed; the panel shows the median. See the integrator for why.
        "mean_read_length":  s["mean_read_length"],
        "median_read_length": s["median_read_length"],
        "mean_qscore":       s["mean_qscore"],
        // Inputs processed so far, and the total when the input set is fixed. A live
        // MinKNOW run has no known total, so `inputs_total` is absent there (null here).
        "inputs_processed":  v["inputs_processed"],
        "inputs_total":      v["inputs_total"],
        // The run-level metric entries, whole: full-length bounds, pooled identity and
        // mismatch sites, poly(A) tail. The Overview picks what it shows.
        "metrics":           v["qc_metrics"]["metrics"],
        // Mean / median / IQR of the per-read mean Q, for the Read quality card.
        "quality_score":     v["snapshots"]["run_summary"]["quality_score"],
        "by_construct":      v["qc_metrics"]["construct_accounting"]["by_construct"],
        "unassigned": {
            "n_reads":       u["n_reads"],
            // Core's ratio, over every read in the run: the Constructs table's last row.
            "share_of_reads": u["share_of_reads"],
            "unmapped":      u["unmapped"],
            "off_reference": u["off_reference"],
        },
        // The flow cell's own account of itself, passed through UNSHAPED. Bucketing 17
        // channel states into something a chart can show, or deciding which end reasons
        // are "abnormal", is a display decision: doing it here would mean a Rust rebuild
        // every time the panel's presentation changes, and would bake a judgement into
        // the layer that is meant to be dumb.
        //
        // Cost is paid once per INPUT, not once per poll — poll_run only rebuilds the
        // panel when latest_snapshot.json's mtime moves, and Core rewrites it per input.
        //
        // Each block carries `available`, and when false an `unavailable_reason`, so the
        // panel can say WHY a section is empty rather than drawing an empty chart.
        // Absence is ordinary: a plain directory of BAMs was never a MinKNOW run, and
        // `voltage` does not exist until the first mux scan, ~93 min into a run.
        "instrument": {
            "pore_activity": v["snapshots"]["pore_activity"],
            "end_reason":    v["snapshots"]["end_reason"],
            "voltage":       v["snapshots"]["voltage"],
        },
        // Analyte shape, also passed through unshaped. These two slots are genuinely
        // RUN-LEVEL: the engine feeds every read to the run-level modules before any
        // construct routing, so they pool the whole run -- unmapped reads included, which
        // is why they do NOT equal the sum over constructs. The panel says so.
        //
        // Drawn in TypeScript from the bin geometry that travels with each block, so the
        // GUI needs no plotting library and the interim CoA's matplotlib cost -- already
        // 25% of the per-POD5 budget -- does not grow to put a chart on screen.
        "distributions": {
            "read_length": v["snapshots"]["run_summary"]["read_length_distribution"],
            "polya":       v["snapshots"]["polya"]["polya_distribution"],
            // The per-read mean Q histogram, same bin geometry as the two above.
            "quality_score": v["snapshots"]["run_summary"]["quality_score_distribution"],
            // For one line on short tails: the whole run's rate, or each construct's
            // when targets are set per construct. not_measured without a cutoff.
            "polya_tail":  v["qc_metrics"]["metrics"]["polya_tail"],
            "constructs":  v["qc_metrics"]["constructs"],
        }
    })
}

fn tail(path: &PathBuf, bytes: u64) -> String {
    let mut f = match File::open(path) { Ok(f) => f, Err(_) => return String::new() };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let from = len.saturating_sub(bytes);
    if f.seek(SeekFrom::Start(from)).is_err() { return String::new(); }
    let mut s = String::new();
    let _ = f.read_to_string(&mut s);
    s
}

/// Resolve the Python that runs Core.
///
/// `pixi run` was the launcher until now, which made the GUI unusable on any machine
/// without pixi — that is, on every machine this product is meant to ship to. The
/// packaging spike established that Core needs no pixi at RUNTIME: `<env>/bin/python -m
/// mrnaspect.main` is enough, because the environment already has Core installed. So what the
/// GUI needs is an INTERPRETER PATH, not a package manager.
///
/// Three sources, in order; the first that names an existing FILE wins:
///   1. `$QCRNA_PYTHON` — the explicit override, the same channel shape `$DORADO_SERVER`
///      already uses for the ONT bundle.
///   2. the interpreter shipped inside the app bundle, beside the executable.
///   3. in a development build only (`tauri dev`), the environment staged in the checkout
///      the build was compiled in, `dist/bundle-env`, which `npm run dev` stages first.
///      Until 2026-11-14 this was a checkout the operator named in Settings › Advanced
///      (Core repo); the packaged app never read that field.
///
/// Why (2) is a path into the bundle: the environment there is a BUILT artifact
/// (`scripts/stage_bundle_env.sh`), not the development one, because the development one
/// installs Core as an editable pointer at the build machine's checkout.
///
/// An earlier version of this comment said conda environments are "not relocatable by
/// copy — their shebangs carry the build path — so an environment has to be built where
/// it will live." Half right, and the wrong half mattered: console-script shebangs ARE
/// absolute (`bin/mrnaspect` names its build-time python), but nothing here runs them. The
/// interpreter and its packages relocate fine, which is why this calls `python3 -m
/// mrnaspect.main` and never `bin/mrnaspect`. Measured 2026-10-21: a staged environment copied to
/// a new path renders a full report under `env -i` from `/`. Relocation is also checked
/// directly by `scripts/check_relocation.sh` (10/10 on linux-64 and Darwin arm64).
fn resolve_python() -> Result<PathBuf, String> {
    let mut tried: Vec<String> = Vec::new();

    match std::env::var("QCRNA_PYTHON") {
        Ok(v) if !v.trim().is_empty() => {
            let pb = PathBuf::from(v.trim());
            if pb.is_file() {
                return Ok(pb);
            }
            // An explicit setting that does not resolve is an ERROR, not a cue to fall
            // through. Falling through would run a different Core than the operator named
            // and never say so — the failure mode this project keeps meeting, where the
            // same setting means different things depending on the route in.
            return Err(format!(
                "$QCRNA_PYTHON is set to {}, which is not a file. It must be the python \
                 binary itself, not the environment directory.",
                pb.display()));
        }
        _ => tried.push("$QCRNA_PYTHON (unset)".into()),
    }

    // macOS bundle: Contents/MacOS/<exe> -> Contents/Resources/env/bin/python3.
    // The second candidate covers a flat layout (the binary and `env/` side by side).
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for rel in ["../Resources/env/bin/python3", "env/bin/python3"] {
                let cand = dir.join(rel);
                if cand.is_file() {
                    return Ok(cand);
                }
                tried.push(cand.display().to_string());
            }
        }
    }

    // A development build runs from the checkout it was compiled in. Compiled out of a
    // release build: there CARGO_MANIFEST_DIR is a folder on the build machine, and a
    // shipped app must never look for it.
    #[cfg(debug_assertions)]
    {
        let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../dist/bundle-env/bin/python3");
        if dev.is_file() {
            return Ok(dev);
        }
        tried.push(dev.display().to_string());
    }

    // Concrete paths, not a category. A previous version of this product reported
    // "beside <bundle>" while searching somewhere else entirely, and the operator had no
    // way to see the discrepancy.
    Err(format!(
        "could not find the python that runs Core. Looked at: {}. Set $QCRNA_PYTHON to the \
         python binary inside an environment that has mrnaspect installed.",
        tried.join(", ")))
}

#[tauri::command(async)]
fn start_qc(app: tauri::AppHandle, input_dir: String, reference: String,
            output_dir: String, mode: String, run_name: String, basecall_model: String,
            config: String, realtime: bool, arm: String, manufacturer: String,
            product: String, batch_id: String, sample_id: String, manufacture_date: String,
            operator: String, reference_text: String)
    -> Result<String, String>
{
    // The reference is a file's path or the text pasted into the window
    // (2026-11-13), never both: the window sends one, so two is a bug, said before
    // anything is created.
    if !reference_text.trim().is_empty() && !reference.trim().is_empty() {
        return Err("both a reference file and a pasted reference were given; send one".into());
    }
    // Real-time QC is pod5-only by construction: its watcher releases *.pod5 and nothing
    // else. Refused here rather than letting Core refuse later, so the operator is told
    // before a run directory and a log file exist for a run that cannot start.
    if realtime && mode != "pod5" {
        return Err("live mode watches for arriving POD5 files; it needs mode=pod5".into());
    }
    // The run's arm, from the window's m1Ψ box (I4). Sent as --arm, so it beats the
    // config's sample.arm like every typed field; the config's sample.arms still names the
    // constructs that differ. The box can only say these two, so anything else is a bug.
    if arm != "m1psi" && arm != "U" {
        return Err(format!("arm must be m1psi or U, got {arm:?}"));
    }
    let mut guard = RUN.lock().map_err(|e| e.to_string())?;
    if let Some(st) = guard.as_mut() {
        if st.child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Err("a run is already in progress".into());
        }
    }
    let python = resolve_python()?;
    // WHERE this run writes, asked of Core before anything is created. A blank Output dir
    // or Run name means what the config says, and failing that Core's own default name
    // (run_YYYYMMDD_HHMMSS, local time) -- the same --print-config, with the same layers,
    // that fills the window's hints, so the directory the shell polls and the one Core
    // writes cannot differ. Until 2026-10-28 the shell decided both itself: a blank Output
    // dir became "" -- logs and the polled run_dir relative to wherever the app started --
    // and a blank Run name became gui_<now>, overriding the config's (I8).
    //
    // Core also validates the name there (resolve_run_dir): a separator, "..", "." or an
    // absolute path comes back as run_dir_error, in Core's words, so the shell does not
    // re-implement the rule and cannot disagree with it.
    let place = print_config(&app, &python, &config,
                             &[("--output-dir", output_dir.as_str()),
                               ("--output-name", run_name.as_str())])?;
    if let Some(e) = place["run_dir_error"].as_str() {
        return Err(e.to_string());
    }
    let run_dir = PathBuf::from(place["run_dir"].as_str().ok_or("Core named no run directory")?);
    // A relative output_dir in a config is relative to wherever the app was started -- "/"
    // for a packaged app. Refused rather than guessed.
    if !run_dir.is_absolute() {
        return Err(format!(
            "the output directory resolves to {}, a relative path, which would land wherever \
             the app was started. Set Output dir, or give the config an absolute output_dir.",
            run_dir.display()));
    }
    let (out, dir_name) = match (run_dir.parent(), run_dir.file_name().and_then(|n| n.to_str())) {
        (Some(parent), Some(name)) => (parent.to_path_buf(), name.to_string()),
        _ => return Err(format!("Core named an unusable run directory: {}", run_dir.display())),
    };
    // TWO names, and they are deliberately not the same string.
    //
    // `dir_name` is the run: it goes to --output-name, so the operator's name -- typed, or
    // in the config -- decides the directory. Reusing one CONTINUES that run and reuses
    // its basecall cache, which is Core's documented behaviour and the reason the field
    // has a hint under it.
    //
    // `stem` is the console log, and it is always unique. They used to be one string, so
    // continuing a run would have overwritten the previous attempt's .stdout -- the one
    // place its failure is recorded. Core's default name is new by construction; a NAMED
    // run gets a time suffix, so its logs stay findable by the name.
    let named = place["resolved_config"]["output_name"].as_str()
        .map(|s| !s.trim().is_empty()).unwrap_or(false);
    let stem = if named { format!("{dir_name}_{}", now_s()) } else { dir_name.clone() };
    std::fs::create_dir_all(&out).map_err(|e| e.to_string())?;
    let stdout_path = out.join(format!("{stem}.stdout"));
    let stderr_path = out.join(format!("{stem}.stderr"));
    let mut cmd = Command::new(&python);
    // Its OWN process group, so cancellation can signal the whole tree. Python is now the
    // direct child, but the group is still what cancellation needs: Core spawns dorado and
    // minimap2 beneath it, and a signal to python alone would leave those running.
    #[cfg(unix)]
    cmd.process_group(0);
    // WHERE the ONT bundle is installed, and which device it runs on, are facts about this
    // computer, not about a run. They used to arrive per run: the server through
    // $DORADO_SERVER and the device as --dorado-device. The first was a defect once the
    // window could pass a config -- resolve_tool ranks an explicit config value ABOVE the
    // environment, so a config naming a server silently beat the binary the operator had
    // just picked (I5). Both now travel in the machine layer below, as the first --config,
    // so they have the precedence every other setting has: anything more specific wins.
    let machine = machine_layer(&app)?;
    let machine_str = machine.as_ref().map(|p| p.to_string_lossy().to_string());
    // `mrnaspect-realtime` has no --mode flag and refuses to start unless the RESOLVED config
    // says pod5 — deliberately, because its shipped default is `mode: bam` and a config
    // saying bam almost always means the operator is pointing at bam_pass, where the
    // watcher would find no POD5 and wait forever. So the one thing it needs is written
    // out here.
    //
    // Into the run's own output directory, not a temp file: it then sits beside the logs
    // as a record of what the run was actually given, and it does not vanish while the
    // run is still reading it.
    let cfg_path = out.join(format!("{stem}_realtime.yaml"));
    if realtime {
        std::fs::write(&cfg_path,
            "# written by the GUI: mrnaspect-realtime is pod5-only and takes this from a config.
# Passed as the LAST --config, so it overrides mode and nothing else.
mode: pod5
")
            .map_err(|e| format!("could not write {}: {e}", cfg_path.display()))?;
    }
    let cfg_str = cfg_path.to_string_lossy().to_string();
    // A pasted reference is written beside the run's logs, as the realtime yaml is: Core
    // reads a FASTA file as it always does, and the file stays with the run as the record
    // of the reference it was given. Core checks it at startup (runtime/reference_check),
    // so the shell writes the text and does not judge it.
    let pasted_ref = out.join(format!("{stem}_reference.fa"));
    let pasted_str = pasted_ref.to_string_lossy().to_string();
    let pasted = !reference_text.trim().is_empty();
    if pasted {
        std::fs::write(&pasted_ref, reference_text.as_bytes())
            .map_err(|e| format!("could not write {}: {e}", pasted_ref.display()))?;
    }
    let reference_arg: &str = if pasted { pasted_str.as_str() } else { reference.as_str() };

    // The two drivers differ in more than a flag, so the argument lists are built apart
    // rather than patched from one another:
    //   * `mrnaspect` globs its input set ONCE at startup and stops when that set is done.
    //   * `mrnaspect-realtime` polls for files as they arrive, and takes the MinKNOW RUN ROOT
    //     — it appends `pod5/` itself (`pod5_subdir`, default "pod5"). Handing it the
    //     pod5 directory would make it look for `<pod5 dir>/pod5`. The input field says
    //     which one it wants; see index.html.
    let out_str = out.to_string_lossy().to_string();
    // `-B`, here and wherever this file starts Python: never write bytecode. The app is
    // sealed by a signature over every file in it, and a .pyc written into
    // Contents/Resources on first run is a file that signature does not cover
    // (2026-11-18: `codesign` reported "file modified" after one run). The environment
    // ships compiled (scripts/bundle_bytecode.py), so nothing is lost by it.
    let mut args: Vec<&str> = if realtime {
        vec![
            "-B", "-m", "mrnaspect.realtime.main",
            "--output-dir", &out_str,
            "--output-name", &dir_name,
        ]
    } else {
        vec![
            "-B", "-m", "mrnaspect.main",
            "--output-dir", &out_str,
            "--output-name", &dir_name,
            "--mode", &mode,
        ]
    };
    // The input and the reference only when typed: blank means "what the config says",
    // and a passed "" used to overwrite it (I8). The output is always passed, as resolved
    // above -- the directory the shell polls must be the one Core writes.
    for (flag, value) in [("--input-dir", input_dir.as_str()), ("--reference", reference_arg)] {
        if !value.trim().is_empty() {
            args.push(flag);
            args.push(value.trim());
        }
    }
    // --config LAYERS, and the order is the whole correctness of it, because the last one
    // wins where two collide: this computer's facts first (the least specific), the
    // operator's file second, the GUI's forced `mode: pod5` last. Reversed, the shell's
    // one-key file would be buried under an operator's `mode: bam` and the live driver
    // would refuse; merged here instead of in Core, it would be a second implementation of
    // load_config written in Rust over YAML. read_config builds the SAME stack, so what
    // the window shows is what the run gets. Checked by test_gui_config_prefill.py and
    // test_gui_machine_settings.py, since no compiler sees this.
    if let Some(m) = machine_str.as_deref() {
        args.push("--config");
        args.push(m);
    }
    if !config.trim().is_empty() {
        args.push("--config");
        args.push(config.trim());
    }
    if realtime {
        args.push("--config");
        args.push(&cfg_str);
    }
    // Optional flags are passed ONLY when non-empty, and are never validated here.
    // Whether pod5 mode requires a model is Core's rule — `--basecall-model` resolves
    // against the config's `basecall.model`, which may already carry one — and a second
    // copy of that rule in the GUI is a copy that can disagree with it. An empty string
    // forwarded as `--basecall-model ""` would be worse than not passing the flag: it
    // would override a configured value with nothing.
    if !basecall_model.trim().is_empty() {
        args.push("--basecall-model");
        args.push(basecall_model.trim());
    }
    args.push("--arm");
    args.push(&arm);
    // What the operator declares about the sample (the Sample step), as Core's own flags,
    // which both drivers take (main.SAMPLE_FLAGS). Only when typed, like the model: a
    // blank field means what the config's sample: says.
    for (flag, value) in [("--manufacturer", manufacturer.as_str()),
                          ("--product", product.as_str()),
                          ("--batch-id", batch_id.as_str()),
                          ("--sample-id", sample_id.as_str()),
                          ("--manufacture-date", manufacture_date.as_str()),
                          ("--operator", operator.as_str())] {
        if !value.trim().is_empty() {
            args.push(flag);
            args.push(value.trim());
        }
    }
    let child = cmd
        .args(&args)
        .stdout(Stdio::from(File::create(&stdout_path).map_err(|e| e.to_string())?))
        .stderr(Stdio::from(File::create(&stderr_path).map_err(|e| e.to_string())?))
        .spawn()
        .map_err(|e| format!("could not start {}: {e}", python.display()))?;
    *guard = Some(RunState {
        child, run_dir: out.join(&dir_name), stdout_path, stderr_path,
        last_mtime: None, figs_mtime: None, last_panel: None, result: None, exit_code: None,
        stop_requested_at: None, started_at: now_s(), finished_at: None,
        last_status: None, status_mtime: None,
    });
    // The run's directory name, which is what the operator typed (or the generated
    // one). Not `stem`: the log prefix carries a timestamp the run itself does not.
    Ok(dir_name)
}

/// What a run would actually use, asked of Core instead of guessed here.
///
/// The shell shows these as PLACEHOLDERS, never values. Core resolves the config, so a
/// GUI that wrote the resolved values into the fields would become a second source for
/// them: the form would send back what Core had just told it, turning a blank "use the
/// default" into an explicit setting, and freezing today's shipped defaults into every
/// later run. A placeholder says "leave this blank and you get that" and cannot be sent.
///
/// `--print-config` creates nothing and needs no reference, so this is safe to call on
/// every edit of the Config field.
///
/// `(async)` because it WAITS on a child process. A plain `#[tauri::command]` runs on the
/// main thread, so the ~0.3-0.5 s python startup would freeze the window every time the
/// operator leaves the Config field -- and once on launch. Nothing here is async
/// Rust; the attribute only moves the call onto a worker. Every other command in this
/// file either returns at once or spawns without waiting, which is why this is the first
/// one that needs it.
#[tauri::command(async)]
fn read_config(app: tauri::AppHandle, config: String)
    -> Result<Value, String>
{
    let python = resolve_python()?;
    print_config(&app, &python, &config, &[])
}

/// Which folder a run of this kind reads, from any level of a MinKNOW run the operator
/// picked (Core's `--describe-input`; 2026-11-12: which depth to pick was not clear).
/// The same config layers as `print_config`, so the run's `realtime.pod5_subdir` is the
/// one counted, and the count is Core's own glob. A blank `path` asks for MinKNOW's data
/// folder. `(async)` for the same reason as `read_config`: it waits on python.
#[tauri::command(async)]
fn describe_input(app: tauri::AppHandle, config: String, path: String,
                  mode: String, live: bool) -> Result<Value, String>
{
    let python = resolve_python()?;
    let mut cmd = Command::new(&python);
    cmd.args(["-B", "-m", "mrnaspect.main", "--describe-input", "--mode", mode.as_str()]);
    if live {
        cmd.arg("--live");
    }
    if let Some(m) = machine_layer(&app)? {
        cmd.arg("--config").arg(m);
    }
    if !config.trim().is_empty() {
        cmd.arg("--config").arg(config.trim());
    }
    cmd.arg("--input-dir").arg(path.trim());
    let out = cmd.output()
        .map_err(|e| format!("could not run {}: {e}", python.display()))?;
    if !out.status.success() {
        let msg = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if msg.is_empty() { "mrnaspect --describe-input failed".into() } else { msg });
    }
    serde_json::from_slice(&out.stdout)
        .map_err(|e| format!("--describe-input did not return JSON: {e}"))
}

/// Core's answer to "what would this run use": `--print-config` with the config layers in
/// the order start_qc passes them. ONE function for both askers -- the window's hints, and
/// start_qc deciding where the run writes -- so they cannot be asked two different
/// questions; until I8 they were, and three of the four path hints were false for the run.
/// `extra` is the window's own values on top, passed only when typed.
fn print_config(app: &tauri::AppHandle, python: &Path, config: &str, extra: &[(&str, &str)])
    -> Result<Value, String>
{
    let mut cmd = Command::new(python);
    cmd.args(["-B", "-m", "mrnaspect.main", "--print-config"]);
    // Same layers, same order as start_qc, or "what would this run use" answers a
    // different question from the one the run asks.
    if let Some(m) = machine_layer(app)? {
        cmd.arg("--config").arg(m);
    }
    if !config.trim().is_empty() {
        cmd.arg("--config").arg(config.trim());
    }
    for &(flag, value) in extra {
        if !value.trim().is_empty() {
            cmd.arg(flag).arg(value.trim());
        }
    }
    let out = cmd.output()
        .map_err(|e| format!("could not run {}: {e}", python.display()))?;
    if !out.status.success() {
        // Core's own line, not a summary of it. A refusal writes the JSON result to
        // stdout and `ERROR: <what to fix>` to stderr, and the second is the one a
        // person can act on.
        let msg = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if msg.is_empty() { "mrnaspect --print-config failed".into() } else { msg });
    }
    serde_json::from_slice(&out.stdout)
        .map_err(|e| format!("--print-config did not return JSON: {e}"))
}

// --- this computer ---------------------------------------------------------------------
// Facts about THIS MAC, not about a run: where the ONT server binary is, which device it
// basecalls on, and the folder the dorado models live in. Entered once, kept here, and
// handed to Core as the FIRST --config layer, so a run config and the run form both
// override them -- the precedence every other setting already has.
//
// One owner: settings.json, which the app writes. machine.yaml is DERIVED from it on every
// save and every run, never edited. It is JSON text, because JSON is valid YAML and
// serde_json quotes a path with spaces, a colon, quotes or `#` exactly -- every one of
// which a hand-written YAML string gets wrong (test_gui_machine_settings.py loads that
// shape through Core's own loader).
//
// No default device, on purpose (2026-10-05): which strings a dorado-server build accepts
// is that build's --help, measured on one Apple-silicon machine and not on the population.

#[derive(Serialize, Deserialize, Default)]
struct Machine {
    #[serde(default)]
    server: String,
    #[serde(default)]
    device: String,
    #[serde(default)]
    models_dir: String,
}

fn machine_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("could not create {}: {e}", dir.display()))?;
    Ok(dir)
}

fn read_machine(app: &tauri::AppHandle) -> Result<Machine, String> {
    let path = machine_dir(app)?.join("settings.json");
    match std::fs::read_to_string(&path) {
        // Not set up yet: the ordinary first launch, not an error.
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Machine::default()),
        Err(e) => Err(format!("could not read {}: {e}", path.display())),
        // A file that does not parse is REPORTED, not replaced by defaults. Defaults would
        // quietly forget the server path, and the next run would fail somewhere else, far
        // from the cause.
        Ok(text) => serde_json::from_str(&text)
            .map_err(|e| format!("{} is not valid settings JSON: {e}", path.display())),
    }
}

/// The Core config layer for this computer, or None when nothing is set.
fn machine_layer(app: &tauri::AppHandle) -> Result<Option<PathBuf>, String> {
    let m = read_machine(app)?;
    let mut server = serde_json::Map::new();
    if !m.server.trim().is_empty() {
        server.insert("binary".into(), json!(m.server.trim()));
    }
    if !m.device.trim().is_empty() {
        server.insert("device".into(), json!(m.device.trim()));
    }
    let path = machine_dir(app)?.join("machine.yaml");
    if server.is_empty() {
        // Nothing to say. The file is removed so it cannot go on saying an earlier setting;
        // if that fails it is still harmless, because a None here means it is never passed.
        let _ = std::fs::remove_file(&path);
        return Ok(None);
    }
    let text = serde_json::to_string_pretty(&json!({ "basecall": { "server": server } }))
        .map_err(|e| e.to_string())?;
    std::fs::write(&path, text)
        .map_err(|e| format!("could not write {}: {e}", path.display()))?;
    Ok(Some(path))
}

#[tauri::command]
fn load_machine(app: tauri::AppHandle) -> Result<Machine, String> {
    read_machine(&app)
}

#[tauri::command]
fn save_machine(app: tauri::AppHandle, server: String, device: String, models_dir: String)
    -> Result<(), String>
{
    let m = Machine { server: server.trim().into(), device: device.trim().into(),
                      models_dir: models_dir.trim().into() };
    let path = machine_dir(&app)?.join("settings.json");
    let text = serde_json::to_string_pretty(&m).map_err(|e| e.to_string())?;
    std::fs::write(&path, text)
        .map_err(|e| format!("could not write {}: {e}", path.display()))?;
    machine_layer(&app).map(|_| ())
}

/// The models at `models_dir`: the folder itself when it is one, otherwise every subfolder
/// that is. A `config.toml` is what marks a dorado model folder, stock and fine-tuned
/// alike (all sixteen on one host, and every model in both ONT server bundles there,
/// 2026-10-28). Name for the list, path for the run.
///
/// The folder itself counts so that one call serves both callers: This Mac's models
/// folder, and the Model row's Browse, which picks ONE model for one run -- the way to
/// give a run a model when the config names a wrong one and This Mac lists none. An ONT
/// server bundle keeps its models a level down (models/ or data/); that is not searched,
/// and the window says where to point instead.
///
/// A model is chosen by NAME per run because which model basecalled the data is a
/// scientific choice the report records -- not a path to dig for every time.
#[tauri::command]
fn list_models(models_dir: String) -> Result<Vec<Value>, String> {
    if models_dir.trim().is_empty() {
        return Ok(vec![]);
    }
    let dir = PathBuf::from(models_dir.trim());
    if dir.join("config.toml").is_file() {
        if let Some(name) = dir.file_name().and_then(|n| n.to_str()) {
            return Ok(vec![json!({ "name": name, "path": dir.to_string_lossy().to_string() })]);
        }
    }
    let entries = std::fs::read_dir(&dir)
        .map_err(|e| format!("could not read {}: {e}", dir.display()))?;
    let mut found: Vec<(String, String)> = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|e| format!("could not list {}: {e}", dir.display()))?;
        let path = entry.path();
        if path.join("config.toml").is_file() {
            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                found.push((name.to_string(), path.to_string_lossy().to_string()));
            }
        }
    }
    found.sort();
    Ok(found.into_iter().map(|(name, path)| json!({ "name": name, "path": path })).collect())
}

#[tauri::command]
fn poll_run() -> Result<Poll, String> {
    let mut guard = RUN.lock().map_err(|e| e.to_string())?;
    let st = match guard.as_mut() {
        None => return Ok(Poll { state: "idle", changed: false, figures_changed: false,
                                 age_s: None, panel: None,
                                 stopping_for_s: None, elapsed_s: None, finished_at: None,
                                 result: None, exit_code: None, log_tail: String::new(),
                                 note: None, status: None }),
        Some(st) => st,
    };

    if st.result.is_none() {
        if let Some(status) = st.child.try_wait().map_err(|e| e.to_string())? {
            st.exit_code = status.code();
            st.finished_at = Some(now_s());
            let text = std::fs::read_to_string(&st.stdout_path).unwrap_or_default();
            st.result = text.lines().rev().find(|l| !l.trim().is_empty())
                .and_then(|l| serde_json::from_str::<Value>(l).ok())
                .or(Some(Value::Null));
        }
    }

    let snap = st.run_dir.join("latest_snapshot.json");
    let m = mtime_s(&snap);
    let mut changed = false;
    if let Some(mn) = mtime_ns(&snap) {
        if st.last_mtime != Some(mn) {
            if let Ok(text) = std::fs::read_to_string(&snap) {
                if let Ok(v) = serde_json::from_str::<Value>(&text) {
                    st.last_panel = Some(extract_panel(&v));
                    st.last_mtime = Some(mn);
                    changed = true;
                }
            }
        }
    }
    // A few hundred bytes, so read whenever it moves.
    let sp = st.run_dir.join("run_status.json");
    if let Some(sm) = mtime_ns(&sp) {
        if st.status_mtime != Some(sm) {
            if let Ok(text) = std::fs::read_to_string(&sp) {
                if let Ok(v) = serde_json::from_str::<Value>(&text) {
                    st.last_status = Some(v);
                    st.status_mtime = Some(sm);
                }
            }
        }
    }
    // Only the stamp here: the file is 2-4 MB, read when the GUI asks for it.
    let fm = mtime_ns(&st.run_dir.join("figures.json"));
    let figures_changed = fm.is_some() && fm != st.figs_mtime;
    if figures_changed { st.figs_mtime = fm; }

    let finished = st.result.is_some();
    let note = if m.is_none() {
        Some(if finished {
            "the run produced no snapshot - it failed before finishing an input".to_string()
        } else {
            "no input has finished yet".to_string()
        })
    } else { None };

    Ok(Poll {
        // "stopping" is a THIRD state, not a flavour of running: the run is still
        // consuming its current input and will stop at the next boundary. Reporting it as
        // "running" is what made the stop look like it had not registered.
        state: if finished { "finished" }
               else if st.stop_requested_at.is_some() { "stopping" }
               else { "running" },
        stopping_for_s: match (finished, st.stop_requested_at) {
            (false, Some(t)) => Some(now_s().saturating_sub(t)),
            _ => None,
        },
        changed,
        figures_changed,
        elapsed_s: Some(st.finished_at.unwrap_or_else(now_s).saturating_sub(st.started_at)),
        finished_at: st.finished_at,
        age_s: m.map(|m| now_s().saturating_sub(m)),
        panel: st.last_panel.clone(),
        result: st.result.clone(),
        exit_code: st.exit_code,
        log_tail: tail(&st.stderr_path, 4000),
        note,
        status: st.last_status.clone(),
    })
}

#[tauri::command]
fn cancel_run() -> Result<(), String> {
    let mut guard = RUN.lock().map_err(|e| e.to_string())?;
    let st = match guard.as_mut() { None => return Ok(()), Some(st) => st };
    if st.child.try_wait().map_err(|e| e.to_string())?.is_some() { return Ok(()); }
    // First press only: the elapsed time the operator is shown must count from when they
    // ASKED, not from the most recent click, or pressing again resets it to zero and hides
    // exactly the fact they are trying to read — how long the stop has been pending.
    if st.stop_requested_at.is_none() {
        st.stop_requested_at = Some(now_s());
    }
    // Negative pid = the whole process group, which is why start_qc made one; that is
    // what reaches the basecaller and aligner Core spawned, not just python itself.
    // SIGTERM rather than SIGKILL: Core routes SIGINT/SIGTERM to a graceful abort that
    // still finalizes and writes a user_abort CoA with whatever it had, whereas SIGKILL
    // throws the partial result away.
    //
    // The kill(2) SYSCALL, not the `kill` binary. Measured 2026-09-23:
    // `/usr/bin/kill -TERM -<pgid>` parses the negative pid as an OPTION, does nothing,
    // and **exits 0** — so even checking the status would not have caught it, and the
    // cancel was a silent no-op through every layer. `--` fixes it on util-linux but the
    // GUI runs on macOS, whose BSD kill parses differently; a feature whose failure is
    // silent should not depend on a shell utility's argument grammar.
    let pgid = st.child.id() as i32;
    let rc = unsafe { libc::kill(-pgid, libc::SIGTERM) };
    if rc != 0 {
        return Err(format!("kill(-{pgid}, SIGTERM) failed: {}",
                           std::io::Error::last_os_error()));
    }
    Ok(())
}


// --- showing the operator where the output is ---------------------------------------
// Measured on 2026-09-23: a run finished, the result object named `report` and `run_dir`
// as absolute paths, and the operator still could not find the files. Printing a path and
// getting someone to the file are different things, and on macOS the gap is literal —
// /tmp is a symlink to /private/tmp and Finder does not browse to it.
//
// Neither command takes a PATH. They read the run this app started, so there is no
// arbitrary-path surface to reason about and nothing for the front end to get wrong: the
// only thing it can ask for is "the report of the run you are holding".
fn launch(program: &str, args: &[&std::ffi::OsStr]) -> Result<(), String> {
    Command::new(program).args(args).spawn()
        .map(|_| ())
        // Not waited on: `open` returns at once but `xdg-open` can block for the life of
        // the viewer. A failure to LAUNCH (no such command) is the one worth reporting
        // and is the one spawn() catches.
        .map_err(|e| format!("could not run `{program}`: {e}"))
}

fn result_path(key: &str) -> Result<PathBuf, String> {
    let guard = RUN.lock().map_err(|e| e.to_string())?;
    let st = guard.as_ref().ok_or("no run in this session yet")?;
    let res = st.result.as_ref().ok_or("the run has not finished yet")?;
    let p = res.get(key).and_then(|v| v.as_str())
        .ok_or_else(|| format!("this run produced no {key} (it may have failed early)"))?;
    let p = PathBuf::from(p);
    if !p.exists() {
        return Err(format!("{} is gone — /tmp is cleared periodically, so a run worth \
                            keeping needs a durable output directory", p.display()));
    }
    Ok(p)
}

/// While the run is going: the interim report Core re-renders every few inputs
/// (reporter.LATEST_HTML_NAME), if one has been written yet.
fn interim_report() -> Option<PathBuf> {
    let guard = RUN.lock().ok()?;
    let st = guard.as_ref()?;
    let p = st.run_dir.join("report_latest.html");
    if p.exists() { Some(p) } else { None }
}

#[tauri::command]
fn open_report() -> Result<(), String> {
    // The final report once the run has one; until then the interim one, so the run
    // card's "Interim report" opens something. The final report's error is the one
    // reported when neither exists.
    let p = match result_path("report") {
        Ok(p) => p,
        Err(e) => interim_report().ok_or(e)?,
    };
    #[cfg(target_os = "macos")]
    return launch("open", &[p.as_os_str()]);
    #[cfg(not(target_os = "macos"))]
    return launch("xdg-open", &[p.as_os_str()]);
}

/// The figure specs Core wrote beside the report, so the GUI draws the SAME charts.
///
/// Read from the run directory rather than threaded through the CLI result: the result
/// JSON has a fixed key contract, and a chart list is not a QC fact. Core builds every
/// spec once in Python and both surfaces consume the file — a TypeScript port of
/// `figspec` would be two implementations of one contract.
///
/// Ok(None) means "not written yet": every render writes it (interims too, since
/// 2026-11-09), so until the first interim, which comes once the run passes `min_reads`.
/// The caller must not read that as "this run has no figures". `Poll.figures_changed`
/// says when to read it again.
#[tauri::command]
fn read_figures() -> Result<Option<Value>, String> {
    let guard = RUN.lock().map_err(|e| e.to_string())?;
    let st = match guard.as_ref() { None => return Ok(None), Some(st) => st };
    let path = st.run_dir.join("figures.json");
    let text = match std::fs::read_to_string(&path) {
        Err(_) => return Ok(None),
        Ok(t) => t,
    };
    // A parse failure is reported, not swallowed: an unreadable figures.json means the
    // render wrote something wrong, and silently showing no charts would hide that.
    serde_json::from_str(&text)
        .map(Some)
        .map_err(|e| format!("{} is not valid JSON: {e}", path.display()))
}

/// The `provenance` block of this run's qc_result.json, for the run card's Details.
///
/// Ok(None) before Core has written the file (it is written once, at the end), so the
/// caller shows the start form's values until then. Like `read_figures` it takes no
/// path: it reads the run this app is holding. A file that does not parse is an error,
/// not None, because it means the run wrote something wrong.
#[tauri::command]
fn read_provenance() -> Result<Option<Value>, String> {
    let guard = RUN.lock().map_err(|e| e.to_string())?;
    let st = match guard.as_ref() { None => return Ok(None), Some(st) => st };
    let path = st.run_dir.join("qc_result.json");
    let text = match std::fs::read_to_string(&path) {
        Err(_) => return Ok(None),
        Ok(t) => t,
    };
    let v: Value = serde_json::from_str(&text)
        .map_err(|e| format!("{} is not valid JSON: {e}", path.display()))?;
    Ok(v.get("provenance").cloned())
}

#[tauri::command]
fn reveal_run_dir() -> Result<(), String> {
    // The run folder Core named before the run began, so the button works DURING a run.
    // It read the result object until 2026-11-12, and while a run was going it answered
    // "the run has not finished yet" from a button that was already enabled.
    let p = {
        let guard = RUN.lock().map_err(|e| e.to_string())?;
        guard.as_ref().ok_or("no run in this session yet")?.run_dir.clone()
    };
    if !p.exists() {
        return Err(format!("{} does not exist yet", p.display()));
    }
    // `open -R` selects the item in Finder rather than opening it, which is what "reveal"
    // means on macOS. Elsewhere there is no reveal, so the directory is opened instead.
    #[cfg(target_os = "macos")]
    return launch("open", &[std::ffi::OsStr::new("-R"), p.as_os_str()]);
    #[cfg(not(target_os = "macos"))]
    return launch("xdg-open", &[p.as_os_str()]);
}

/// The third parties' notices this build carries: `scripts/make_notices.py` writes the
/// file into the staged environment, which the bundle ships as `Resources/env`.
///
/// Looked for where `resolve_python` looks for the interpreter, and for the same reason:
/// inside the .app first, then beside the binary, then, in a development build, the staged
/// tree of the checkout it was compiled in. The name is the script's `NAME`;
/// test_third_party_notices.py holds the two equal.
fn notices_path() -> Result<PathBuf, String> {
    let mut tried: Vec<String> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for rel in ["../Resources/env/THIRD-PARTY-NOTICES.txt",
                        "env/THIRD-PARTY-NOTICES.txt"] {
                let cand = dir.join(rel);
                if cand.is_file() {
                    return Ok(cand);
                }
                tried.push(cand.display().to_string());
            }
        }
    }
    #[cfg(debug_assertions)]
    {
        let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../dist/bundle-env/THIRD-PARTY-NOTICES.txt");
        if dev.is_file() {
            return Ok(dev);
        }
        tried.push(dev.display().to_string());
    }
    Err(format!("this build carries no third-party notices. Looked at: {}", tried.join(", ")))
}

/// Opens the notices in the system's viewer of text files. Like `open_report` it takes no
/// path: the only file it can open is the one this build carries.
#[tauri::command]
fn open_notices() -> Result<(), String> {
    let p = notices_path()?;
    #[cfg(target_os = "macos")]
    return launch("open", &[p.as_os_str()]);
    #[cfg(not(target_os = "macos"))]
    return launch("xdg-open", &[p.as_os_str()]);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![start_qc, poll_run, cancel_run, open_report,
                                             reveal_run_dir, read_figures, read_config,
                                             describe_input, load_machine, save_machine,
                                             list_models, read_provenance, open_notices])
        // Closing the window must not orphan Core. SIGTERM, not kill: Core routes it to
        // a graceful abort that still finalizes and writes a user_abort CoA with whatever
        // it had. Without this block the app still works; it only leaves the child
        // running when the window is closed.
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let _ = cancel_run();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
