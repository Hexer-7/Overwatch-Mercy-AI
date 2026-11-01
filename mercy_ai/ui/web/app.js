/* SPDX-License-Identifier: Apache-2.0
 * Copyright (c) 2024-2026 Hexer-7 */

/* Mercy AI desktop window — the page half of the bridge.
 *
 * Deliberately plain: no framework, no build step, no bundler. The whole page
 * is one 520x700 window with four tabs and a wizard, and every value it shows
 * comes from Python. Adding a toolchain would mean this file could go stale
 * against a `.js` nobody rebuilt, which is a strange failure to introduce into
 * a program whose other half is already tested.
 *
 * Three conventions carry the state:
 *   - `data-active` / `data-switch` / `data-check` / `data-radio` attributes,
 *     styled entirely in app.css, so this file never writes a colour.
 *   - `save({field: value})` for every settings change. Python validates, writes
 *     config.xml atomically and hands back the config it actually stored, which
 *     is what the page then re-renders from. The page never assumes its own
 *     edit succeeded.
 *   - Every setting's explanation comes from `config.descriptions`, which Python
 *     builds from the same comments it writes into config.xml. There is no
 *     second copy of that prose to drift out of date.
 */

"use strict";

const $ = (id) => document.getElementById(id);
const all = (sel) => Array.from(document.querySelectorAll(sel));

let api = null;
let config = {};
let env = {};
let tab = "status";
let capturing = null;      // which binding is waiting for a keypress
/* The mouse button that just set the trigger. Its own click, auxclick or
   context menu still follows the mousedown, and must not reach whatever is
   under the pointer. */
let swallowMouse = null;
let advancedOpen = false;
/* The Practice Range notice is shown the first time Advanced is opened in a
   session, not every time. */
let advancedNoticed = false;
let statusTimer = null;
/* Why this is not just written straight into the banner: renderStatus runs
   from a 120 ms poll and rewrites the same element, so a message put there
   by a button handler would be erased about a tenth of a second later and
   look exactly like the button doing nothing. */
let actionProblems = [];

/* The wizard's own state, kept out of `config` until the user finishes: an
   abandoned setup must not leave a half-written config.xml behind. */
const wiz = { step: 1, ack: false, heal: null, boost: null, resolution: null };

const TRIGGER_LABELS = { left_mouse: "Mouse Left click", right_mouse: "Mouse Right click" };
const triggerLabel = (v) => TRIGGER_LABELS[v] || v;
const fmt3 = (v) => Number(v || 0).toFixed(3);
/* "F2" and "R" read better than "f2" and "r" in a sentence; "page up" stays. */
const keyName = (k) => (k && k.length <= 3 ? k.toUpperCase() : k || "");
const pct = (v) => Math.max(0, Math.min(100, Number(v || 0) * 100));
const describe = (field) => (config.descriptions || {})[field] || "";

/* The Settings page is laid out in index.html: the few things a new player
   touches up top, everything else under Advanced. Measured tuning constants
   (capture flags, Flash Heal filters and the like) are not shown at all; they
   stay in config.xml for anyone who needs them. */

const THEMES = [["auto", "Auto"], ["light", "Light"], ["dark", "Dark"]];

/* ── bridge helpers ─────────────────────────────────────────────────────── */

function showProblems(node, problems) {
  if (!problems || !problems.length) { node.hidden = true; return; }
  node.innerHTML = "<ul>" + problems.map((p) => "<li>" + escapeHtml(p) + "</li>").join("") + "</ul>";
  node.hidden = false;
}

function escapeHtml(text) {
  const box = document.createElement("div");
  box.textContent = text;
  return box.innerHTML;
}

/* Every settings write goes through here so there is one place that re-renders
   from what Python stored rather than from what the page hoped it stored. */
async function save(patch) {
  return applyResult(await api.save_settings(patch));
}

/* `field` is set when the change came from a number box. Its problem is then
   shown right under that box -- the player is looking there, usually far below
   the banner at the top of the page. */
function applyResult(result, field) {
  if (result.config) { config = result.config; }
  if (result.ok) { actionProblems = []; }
  all("[data-error]").forEach((node) => { node.hidden = true; });
  const inline = field && !result.ok && document.querySelector('[data-error="' + field + '"]');
  if (inline) {
    inline.textContent = (result.problems || []).join(" ");
    inline.hidden = false;
    showProblems($("settingsProblems"), []);
  } else {
    showProblems($("settingsProblems"), result.problems);
    /* A refused key or switch under Advanced would otherwise report at the top
       of a page scrolled far below it, and look like nothing happened. */
    if (!result.ok && tab === "settings") { $("settingsProblems").scrollIntoView({ block: "nearest" }); }
  }
  /* Applied on every save, so a new theme shows at once rather than at the
     next launch. */
  applyTheme();
  renderSettings();
  renderChrome();
  renderHelp();
  return result.ok;
}

/* ── theme ──────────────────────────────────────────────────────────────── */

function applyTheme() {
  const choice = config.theme || "auto";
  const dark = choice === "dark"
    || (choice === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

/* ── tabs ───────────────────────────────────────────────────────────────── */

function setTab(name) {
  tab = name;
  all(".tab").forEach((b) => b.setAttribute("data-active", String(b.dataset.tab === name)));
  ["status", "settings", "help", "about"].forEach((id) => {
    $("tab" + id[0].toUpperCase() + id.slice(1)).hidden = id !== name;
  });
}

/* ── the persistent chrome: threshold marks and the bottom bar ──────────── */

function renderChrome() {
  $("hintTrigger").textContent = triggerLabel(config.keybind_btn);
  $("hintPause").textContent = config.pause_toggle_button || "f2";

  const threshold = pct(config.threshold);
  $("healthTick").style.left = threshold + "%";
  const label = $("thresholdLabel");
  label.textContent = "Switch " + fmt3(config.threshold);
  /* Near the right-hand end the label would run past the bar, so it anchors to
     the right edge instead of to the tick. */
  if (threshold > 70) {
    label.style.left = "auto";
    label.style.right = (100 - threshold) + "%";
    label.style.textAlign = "right";
  } else {
    label.style.left = threshold + "%";
    label.style.right = "auto";
    label.style.textAlign = "left";
  }

  const res = $("sbRes");
  const mismatch = Boolean(env.detected) && env.detected !== config.resolution;
  res.querySelector("span").textContent = config.resolution + "p"
    + (mismatch ? " — screen is " + env.detected : "");
  res.setAttribute("data-warn", String(mismatch));
}

/* ── the Status tab ─────────────────────────────────────────────────────── */

function renderStatus(s) {
  const word = s.state_word;
  $("stateWord").textContent = word;
  $("stateWord").setAttribute("data-state", word);

  /* What the beam is doing right now, which is the question a player has
     mid-fight; RUNNING alone does not answer it. */
  const mode = s.paused || !s.running ? "—" : s.mode_word;
  $("modeWord").textContent = mode;
  $("modeWord").setAttribute("data-mode", mode);

  $("healthText").textContent = fmt3(s.health);
  $("healthFill").style.width = pct(s.health) + "%";

  /* An average, not the instantaneous rate, which flickers too fast to read
     and tells you nothing the average does not. */
  $("sbFps").querySelector("span").textContent =
    s.fps_avg > 0 ? Math.round(s.fps_avg) + " fps avg" : "— fps";

  $("btnPause").textContent = s.paused ? "Resume" : "Pause";
  $("btnPause").className = s.paused ? "btn btn-primary run-btn" : "btn btn-secondary run-btn";
  $("btnPause").disabled = !s.running;
  showProblems($("statusProblems"), (s.error ? [s.error] : []).concat(actionProblems));
}

async function pollStatus() {
  if (!api) { return; }
  try { renderStatus(await api.get_status()); } catch (err) { /* window closing */ }
}

/* ── the Settings tab ───────────────────────────────────────────────────── */

function segmented(node, options, current, onPick) {
  node.innerHTML = "";
  options.forEach(([value, text]) => {
    const button = document.createElement("button");
    button.className = "btn res-opt";
    button.textContent = text;
    button.setAttribute("data-active", String(value === current));
    button.addEventListener("click", () => onPick(value));
    node.appendChild(button);
  });
}

function bindToggles(scope) {
  Array.from(scope.querySelectorAll("[data-toggle]")).forEach((b) =>
    b.addEventListener("click", () => save({ [b.dataset.toggle]: !config[b.dataset.toggle] })));
}

function bindNumbers(scope) {
  Array.from(scope.querySelectorAll("[data-number]")).forEach((input) =>
    input.addEventListener("change", async () => {
      const field = input.dataset.number;
      applyResult(await api.save_settings({ [field]: input.value }), field);
    }));
}

/* A line under some number boxes that works the setting out against the
   player's own values, so the number means something before it is saved. */
const LIVE = {
  dynamic_heal_drop: () => {
    const resume = Number(config.threshold) - Number(config.dynamic_heal_drop);
    if (resume <= 0) {
      return "With your settings Mercy AI never goes back to healing once damage boost starts, "
        + "until you let go of the trigger. Type a smaller number or move the dial higher.";
    }
    return "With your settings: damage boost from " + fmt3(config.threshold)
      + ", back to healing below " + fmt3(resume) + ".";
  },
  flash_heal_drop: () => {
    const to = Math.max(0, 0.9 - Number(config.flash_heal_drop));
    return "With " + Number(config.flash_heal_drop) + ": a fall from 0.900 to " + fmt3(to)
      + " or lower within 0.3 seconds fires Flash Heal.";
  },
  flash_heal_low_health: () => {
    const line = Number(config.flash_heal_low_health);
    return "With " + line + ": Flash Heal fires once your ally's health match is below "
      + fmt3(line) + ", then waits until they are back above " + fmt3(line + 0.05) + ".";
  },
};

/* This is a screen *height*, and saying so is the whole point. Someone on a
   1440x900 monitor will otherwise pick "1440" because their screen is 1440
   pixels wide, and the capture region for a 2560x1440 screen then lands below
   the bottom of theirs. */
function resolutionNote() {
  const sizes = config.resolution_sizes || {};
  const chosen = sizes[config.resolution]
    ? escapeHtml(config.resolution) + " means a screen " + escapeHtml(config.resolution)
      + " pixels tall (usually " + escapeHtml(sizes[config.resolution]) + ")."
    : "";
  if (!env.primary) { return chosen; }
  if (env.detected) {
    return chosen + " This monitor is " + escapeHtml(env.primary)
      + ", so " + escapeHtml(env.detected) + " is the match.";
  }
  return chosen + " This monitor is " + escapeHtml(env.primary)
    + ", which matches none of them — fine if you are setting up for another computer, "
    + "but Mercy AI cannot watch the screen here.";
}

/* Resolution is detected, so the picker lives under Advanced and the main
   page only speaks up when the screen and the setting disagree. */
function renderResolutionAlert() {
  const box = $("resAlert");
  const fix = $("btnResFix");
  if (env.detected && env.detected !== config.resolution) {
    $("resAlertText").textContent = "Your screen is " + env.primary + ", but Mercy AI is set to "
      + config.resolution + "p.";
    fix.textContent = "Use " + env.detected + "p";
    fix.hidden = false;
    box.hidden = false;
  } else if (env.primary && !env.detected) {
    $("resAlertText").textContent = "This screen is " + env.primary + ". Mercy AI only supports "
      + "screens 2160, 1440, 1080 or 768 pixels tall, so it cannot watch this one.";
    fix.hidden = true;
    box.hidden = false;
  } else {
    box.hidden = true;
  }
}

function renderSettings() {
  renderResolutionAlert();
  $("dial").value = config.threshold_percent;
  $("dialText").textContent = Number(config.threshold_percent).toFixed(1);
  $("dialThreshold").textContent = "match " + fmt3(config.threshold);

  all("[data-bind]").forEach((b) => {
    const value = config[b.dataset.bind];
    b.querySelector("span").textContent =
      capturing === b.dataset.bind ? "press a key…" : (value || "not set");
    b.setAttribute("data-capture", String(capturing === b.dataset.bind));
  });
  const trigger = $("btnTrigger");
  trigger.querySelector("span").textContent =
    capturing === "keybind_btn" ? "press a key or click here…" : triggerLabel(config.keybind_btn);
  trigger.setAttribute("data-capture", String(capturing === "keybind_btn"));

  segmented($("resRow"), (config.resolutions || []).map((r) => [r, r]),
            config.resolution, (value) => save({ resolution: value }));
  $("resNote").innerHTML = resolutionNote();
  $("resNote").setAttribute("data-warn", String(Boolean(env.primary) && !env.detected));

  all("[data-toggle]").forEach((b) =>
    b.setAttribute("data-switch", String(Boolean(config[b.dataset.toggle]))));
  const ranges = config.ranges || {};
  all("[data-number]").forEach((input) => {
    const field = input.dataset.number;
    input.value = config[field];
    if (ranges[field]) { input.min = ranges[field][0]; input.max = ranges[field][1]; }
  });
  all("[data-desc]").forEach((node) => { node.textContent = describe(node.dataset.desc); });
  all("[data-live]").forEach((node) => {
    const line = LIVE[node.dataset.live];
    node.textContent = line ? line() : "";
  });

  $("advSmooth").hidden = !config.dynamic_heal;
  /* Part of the focus check, so it only shows while that is on. */
  $("clickCheckRow").hidden = !config.focus_gate;

  /* One switch for the whole feature: it is either doing something or it is
     not. Which triggers it uses is an Advanced question. */
  const flashOn = Boolean(config.flash_heal_enabled);
  $("flashEnable").setAttribute("data-switch", String(flashOn));
  const when = [];
  if (config.flash_heal_trigger === "expiry") {
    when.push("when Ana's grenade wears off your ally, if they are below half health");
  }
  if (config.flash_heal_on_health_drop) {
    when.push(config.flash_heal_health_mode === "low"
      ? "when your ally is below " + fmt3(config.flash_heal_low_health)
      : "when your ally suddenly drops");
  }
  $("flashCaption").textContent = flashOn
    ? "Casts Flash Heal (key " + keyName(config.flash_heal_btn) + ") " + when.join(", and ") + "."
    : "Off. Turn on to counter Ana's anti-heal grenade.";
  $("advFlash").hidden = !flashOn;
  $("flashAna").setAttribute("data-switch", String(config.flash_heal_trigger === "expiry"));
  const mode = config.flash_heal_health_mode === "low" ? "low" : "emergency";
  all("[data-health-mode]").forEach((b) =>
    b.setAttribute("data-active", String(b.dataset.healthMode === mode)));
  all("[data-health-field]").forEach((n) => { n.hidden = !config.flash_heal_on_health_drop; });
  all("[data-mode-field]").forEach((n) => {
    n.hidden = !config.flash_heal_on_health_drop || n.dataset.modeField !== mode;
  });

  segmented($("themeRow"), THEMES, config.theme || "auto", (value) => save({ theme: value }));
  renderResets();
}

/* ── Reset buttons ──────────────────────────────────────────────────────── */

/* Each Reset names the setting it puts back in `data-reset`, and shows only
   while it differs from its default. Only numbers and keys have one: a switch
   is put back by clicking it again. Python sends the defaults
   (config.defaults); the heal and damage boost keys and the resolution have
   none, because only the player can choose them, so they never get one.

   Hidden with `visibility`, not `hidden`: the button keeps its space, so
   nothing beside it moves when it appears. */
const resetFields = (button) => button.dataset.reset.split(" ");

function isDefault(field) {
  const defaults = config.defaults || {};
  if (!(field in defaults)) { return true; }
  const want = defaults[field];
  /* 0.15 typed in a box comes back as 0.15, but compare as numbers anyway so
     10 and 10.0 are the same. */
  if (typeof want === "number") { return Math.abs(Number(config[field]) - want) < 1e-9; }
  return config[field] === want;
}

function defaultWords(field) {
  const value = (config.defaults || {})[field];
  if (typeof value === "boolean") { return value ? "on" : "off"; }
  if (field === "keybind_btn") { return triggerLabel(value); }
  if (field === "pause_toggle_button" || field === "flash_heal_btn" || field === "scoreboard_btn") {
    return keyName(value);
  }
  if (typeof value === "string") { return value.charAt(0).toUpperCase() + value.slice(1); }
  return String(value);
}

function renderResets() {
  all("[data-reset]").forEach((button) => {
    const fields = resetFields(button);
    const changed = fields.some((field) => !isDefault(field));
    button.setAttribute("data-shown", String(changed));
    button.tabIndex = changed ? 0 : -1;
    button.title = "Back to the default: " + fields.map(defaultWords).join(", ");
  });
}

/* ── the Help tab ───────────────────────────────────────────────────────── */

const TICK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M20 6 9 17l-5-5"/></svg>';
const CROSS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M12 3 2 20h20L12 3ZM12 10v4M12 17h.01"/></svg>';

function renderHelp() {
  const checks = [
    [env.elevated !== false,
     env.elevated === true ? "Running as administrator."
       : env.elevated === false ? "Not running as administrator — fine unless key presses do not reach the game."
       : "Could not tell whether this is running as administrator."],
    [Boolean(env.detected),
     env.detected ? "This screen is " + env.primary + ", which Mercy AI has templates for."
       : "This screen is " + (env.primary || "unknown") + " — Mercy AI has no templates for it and cannot watch it here."],
    [!env.detected || env.detected === config.resolution,
     env.detected === config.resolution
       ? "The configured resolution matches this screen."
       : "Configured for " + config.resolution + "p but this screen is " + (env.detected || "different") + "."],
  ];
  $("healthCheckList").innerHTML = checks.map(([ok, text]) =>
    '<div class="check-item" data-ok="' + ok + '">' + (ok ? TICK : CROSS)
    + "<span>" + escapeHtml(text) + "</span></div>").join("");

  /* The setup steps name the player's actual keys, not an example: in full
     in the sentence, and as a keyboard prints them on the little keycaps. */
  ["helpHealKey2"].forEach((id) => { $(id).textContent = config.heal_btn || "not set"; });
  ["helpBoostKey2"].forEach((id) => { $(id).textContent = config.damage_boost_btn || "not set"; });
  keycap($("helpHealKey"), config.heal_btn);
  keycap($("helpBoostKey"), config.damage_boost_btn);
}

/* What a keyboard prints on a key whose name is too long for a keycap. */
const KEYCAP_LABELS = {
  "insert": "Ins", "delete": "Del", "home": "Home", "end": "End",
  "page up": "PgUp", "page down": "PgDn", "space": "Spc", "backspace": "Bksp",
  "enter": "Ent", "caps lock": "Caps", "shift": "Shift", "ctrl": "Ctrl", "alt": "Alt",
  "esc": "Esc", "tab": "Tab", "up": "\u2191", "down": "\u2193", "left": "\u2190", "right": "\u2192",
};

function keycap(node, key) {
  const label = KEYCAP_LABELS[key] || (key ? key.toUpperCase() : "?");
  node.textContent = label;
  /* Longer labels drop a size so two keycaps still sit side by side. */
  node.setAttribute("data-long", String(label.length > 2));
}

/* ── key capture ────────────────────────────────────────────────────────── */

function beginCapture(field) {
  capturing = field;
  renderSettings();
  renderWizard();
}

async function handleCapture(event) {
  if (!capturing) { return; }
  event.preventDefault();
  if (event.code === "Escape") { capturing = null; renderSettings(); renderWizard(); return; }
  const result = await api.capture_key(event.code, event.key);
  if (!result.ok) {
    /* By where the key is being set, not by tab: Redo setup leaves the tab on
       Settings while the wizard is what is on screen. */
    const box = capturing.startsWith("wiz:") ? $("wizProblems") : $("settingsProblems");
    showProblems(box, result.problems);
    if (box.id === "settingsProblems") { box.scrollIntoView({ block: "nearest" }); }
    return;
  }
  const field = capturing;
  capturing = null;
  if (field.startsWith("wiz:")) {
    wiz[field.slice(4)] = result.key;
    showProblems($("wizProblems"), []);  // an earlier refusal no longer applies
    renderWizard();
  } else {
    await save({ [field]: result.key });
  }
}

/* The trigger may be a mouse button as well as a key. While it is being set,
   pressing Left or Right click *on its own box* picks that button -- the way
   back to the mouse after a key was chosen. A click anywhere
   else gives up and goes on as a normal click, so leaving for another tab
   never sets the trigger by accident. */
const MOUSE_TRIGGERS = { 0: "left_mouse", 2: "right_mouse" };

function handleMouseCapture(event) {
  if (capturing !== "keybind_btn") { return; }
  if (!$("btnTrigger").contains(event.target)) {
    /* Redraw only once this click is over. Redrawn now, the theme and
       resolution rows are rebuilt under the pointer and the trigger box
       shrinks back, moving everything below it -- the button that was
       pressed is gone or elsewhere by mouseup, and the click never lands. */
    capturing = null;
    document.addEventListener("mouseup", () => setTimeout(renderSettings), { once: true, capture: true });
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  swallowMouse = event.button;
  const name = MOUSE_TRIGGERS[event.button];
  if (!name) {
    showProblems($("settingsProblems"),
      ["Only Mouse Left click or Mouse Right click can be the trigger. Click the box with one "
        + "of them, or press a keyboard key."]);
    $("settingsProblems").scrollIntoView({ block: "nearest" });
    return;
  }
  capturing = null;
  save({ keybind_btn: name });
}

function swallowFollowUp(event) {
  if (swallowMouse === null || event.button !== swallowMouse) { return; }
  event.preventDefault();
  event.stopPropagation();
  /* The last event each button sends: click for Left, the context menu for
     Right, auxclick for the rest. */
  const last = { 0: "click", 2: "contextmenu" }[swallowMouse] || "auxclick";
  if (event.type === last) { swallowMouse = null; }
}

/* ── Advanced and its notice ────────────────────────────────────────────── */

function setAdvanced(open) {
  advancedOpen = open;
  $("advancedCard").hidden = !open;
  $("advancedGlyph").textContent = open ? "−" : "+";
}

function showAdvancedNotice(show) {
  $("advNotice").hidden = !show;
  if (show) { $("btnAdvOk").focus(); }
}

/* ── the first-run wizard ───────────────────────────────────────────────── */

function buildWizardResolutions() {
  const list = $("wizResList");
  const names = { 2160: "4K", 1440: "2K", 1080: "FHD", 768: "HD+" };
  const sizes = config.resolution_sizes || {};
  list.innerHTML = "";
  (config.resolutions || []).forEach((value) => {
    const button = document.createElement("button");
    button.className = "btn res-item";
    button.dataset.wizres = value;
    button.innerHTML =
      '<span><span class="dot-radio"></span><span><span class="res-item-name">'
      + escapeHtml((names[value] || "") + " · " + value) + "</span>"
      + '<span class="res-item-size">' + escapeHtml(sizes[value] || "") + "</span></span></span>"
      + (env.detected === value ? '<span class="tag tag-accent">Detected</span>' : "");
    button.addEventListener("click", () => { wiz.resolution = value; renderWizard(); });
    list.appendChild(button);
  });
  const warn = $("wizResWarn");
  warn.hidden = Boolean(env.detected) || !env.primary;
  /* Not an error. Setting Mercy AI up on one computer to run on another is a
     perfectly ordinary thing to do, and the wizard should say which of the two
     it is asking about rather than assume they are the same machine. */
  warn.textContent = "This monitor is " + (env.primary || "") + ", which matches none of these "
    + "heights. Pick the one your game computer uses — the setting is saved either way. "
    + "Mercy AI just cannot watch the screen on this machine.";
}

function renderWizard() {
  $("stepNumber").textContent = wiz.step;
  $("step2Bar").setAttribute("data-current", String(wiz.step >= 2));
  $("step3Bar").setAttribute("data-current", String(wiz.step >= 3));
  $("wizStep1").hidden = wiz.step !== 1;
  $("wizStep2").hidden = wiz.step !== 2;
  $("wizStep3").hidden = wiz.step !== 3;

  $("ackBox").setAttribute("data-check", String(wiz.ack));
  $("wizHealLabel").textContent = capturing === "wiz:heal" ? "press a key…" : (wiz.heal || "Not set");
  $("wizBoostLabel").textContent = capturing === "wiz:boost" ? "press a key…" : (wiz.boost || "Not set");
  $("wizHealAction").textContent = wiz.heal ? "change" : "set key";
  $("wizBoostAction").textContent = wiz.boost ? "change" : "set key";
  $("btnWizHeal").setAttribute("data-capture", String(capturing === "wiz:heal"));
  $("btnWizBoost").setAttribute("data-capture", String(capturing === "wiz:boost"));
  const clash = wizardKeyProblem();
  $("wizClash").textContent = clash;
  $("wizClash").hidden = !clash;
  $("wizKeyNote").textContent = wizardKeyNote();

  all("[data-wizres]").forEach((b) => {
    const on = b.dataset.wizres === wiz.resolution;
    b.setAttribute("data-active", String(on));
    b.querySelector(".dot-radio").setAttribute("data-radio", String(on));
  });

  $("btnWizBack").disabled = wiz.step === 1;
  $("btnWizNext").textContent = wiz.step === 3 ? "Finish setup" : "Next";
  $("btnWizNext").disabled = !stepIsComplete();
}

function stepIsComplete() {
  if (wiz.step === 1) { return wiz.ack; }
  if (wiz.step === 2) { return Boolean(wiz.heal && wiz.boost) && !wizardKeyProblem(); }
  return Boolean(wiz.resolution);
}

/* Keys Mercy AI already uses for something else. Refused on step 2 itself, in
   plain words, instead of at "Finish setup" with a message about a setting the
   wizard never showed. */
function takenKeys() {
  /* The Flash Heal key only counts while Flash Heal is on; off, nothing presses it. */
  return [
    [config.pause_toggle_button, "your pause key"],
    [config.flash_heal_enabled ? config.flash_heal_btn : "", "your Flash Heal key"],
    [config.flash_heal_enabled && config.flash_heal_scoreboard_hold ? config.scoreboard_btn : "",
     "your scoreboard key"],
    [config.keybind_btn, "your trigger"],
  ].filter(([key]) => key && !String(key).endsWith("_mouse"));
}

function wizardKeyProblem() {
  for (const [role, key] of [["heal", wiz.heal], ["damage boost", wiz.boost]]) {
    const hit = takenKeys().find(([other]) => key && key === other);
    if (hit) { return keyName(key) + " is " + hit[1] + ". Pick a different key for " + role + "."; }
  }
  if (wiz.heal && wiz.heal === wiz.boost) {
    return "Those are the same key. Heal and damage boost need to be different, or they'll fight each other in game.";
  }
  return "";
}

/* Names only the keys that cannot be used -- never a key to pick. */
function wizardKeyNote() {
  const taken = takenKeys().map(([key, what]) => keyName(key) + " (" + what.replace("your ", "") + ")");
  return "Choose any keyboard keys you like"
    + (taken.length ? ", except " + taken.join(" and ") : "")
    + ". Not a mouse button.";
}

async function wizardNext() {
  if (!stepIsComplete()) { return; }
  if (wiz.step < 3) { wiz.step += 1; renderWizard(); return; }
  const result = await api.save_settings({
    heal_btn: wiz.heal,
    damage_boost_btn: wiz.boost,
    resolution: wiz.resolution,
  });
  showProblems($("wizProblems"), result.problems);
  if (!result.ok) { return; }
  config = result.config;
  env.needs_setup = false;
  env.config_error = null;  // the file setup just wrote is a good one
  await enterMain();
}

/* ── boot ───────────────────────────────────────────────────────────────── */

async function enterMain() {
  $("wizard").hidden = true;
  $("main").hidden = false;
  $("brandName").textContent = "Mercy AI";
  renderSettings();
  renderChrome();
  renderHelp();
  setTab("status");
  /* There is no Start button. Watching the screen is what the program is
     for, so it starts doing it -- Pause is how you stop. */
  const started = await api.set_running(true);
  actionProblems = started.ok ? [] : (started.problems || []);
  await pollStatus();
  if (statusTimer === null) { statusTimer = setInterval(pollStatus, 120); }
}

function enterWizard() {
  $("main").hidden = true;
  $("wizard").hidden = false;
  $("brandName").textContent = "Mercy AI — setup";
  wiz.resolution = env.detected || null;
  /* Always from the top, and from the stored keys: empty on a first run, the
     player's current keys on "Redo setup" -- never whatever an earlier pass
     through the wizard left behind, which could undo a change made since. */
  wiz.step = 1;
  wiz.ack = false;
  wiz.heal = config.heal_btn || null;
  wiz.boost = config.damage_boost_btn || null;
  showProblems($("wizProblems"), []);
  const broken = $("wizConfigError");
  broken.hidden = !env.config_error;
  broken.textContent = env.config_error
    ? "config.xml could not be read. " + env.config_error + " Finishing setup writes a new "
      + "one with default settings. To keep your old settings instead, close Mercy AI, fix the "
      + "file and start it again."
    : "";
  buildWizardResolutions();
  renderWizard();
}

function wire() {
  $("btnMinimize").addEventListener("click", () => api.minimize());
  $("btnClose").addEventListener("click", () => api.close());
  all(".tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

  $("btnPause").addEventListener("click", async () => { await api.toggle_pause(); pollStatus(); });

  /* `input` is every pixel of the drag and `change` is the release. Saving on
     `input` would rewrite config.xml and restart the loop dozens of times per
     drag, so only the label follows the thumb. */
  $("dial").addEventListener("input", () => {
    $("dialText").textContent = Number($("dial").value).toFixed(1);
  });
  $("dial").addEventListener("change", () => save({ threshold_percent: $("dial").value }));

  all("[data-bind]").forEach((b) => b.addEventListener("click", () => beginCapture(b.dataset.bind)));
  $("btnTrigger").addEventListener("click", () => beginCapture("keybind_btn"));

  bindToggles(document);
  bindNumbers(document);
  all("[data-reset]").forEach((button) => button.addEventListener("click", () => {
    const patch = {};
    resetFields(button).forEach((field) => { patch[field] = config.defaults[field]; });
    save(patch);
  }));

  /* Off clears both triggers; on restores the shipped defaults for both. */
  $("flashEnable").addEventListener("click", () => save(config.flash_heal_enabled
    ? { flash_heal_trigger: "off", flash_heal_on_health_drop: false }
    : { flash_heal_trigger: "expiry", flash_heal_on_health_drop: true }));
  $("btnResFix").addEventListener("click", () => save({ resolution: env.detected }));
  $("flashAna").addEventListener("click", () =>
    save({ flash_heal_trigger: config.flash_heal_trigger === "expiry" ? "off" : "expiry" }));
  all("[data-health-mode]").forEach((b) =>
    b.addEventListener("click", () => save({ flash_heal_health_mode: b.dataset.healthMode })));

  $("btnAdvanced").addEventListener("click", () => {
    if (!advancedOpen && !advancedNoticed) { showAdvancedNotice(true); return; }
    setAdvanced(!advancedOpen);
  });
  $("btnAdvOk").addEventListener("click", () => {
    advancedNoticed = true;
    showAdvancedNotice(false);
    setAdvanced(true);
  });
  $("btnAdvCancel").addEventListener("click", () => showAdvancedNotice(false));
  $("advNotice").addEventListener("click", (event) => {
    if (event.target === $("advNotice")) { showAdvancedNotice(false); }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("advNotice").hidden) { showAdvancedNotice(false); }
  });
  $("btnOpenFolder").addEventListener("click", async () =>
    showProblems($("settingsProblems"), (await api.open_folder()).problems));
  $("btnRedoSetup").addEventListener("click", () => { env.needs_setup = true; enterWizard(); });
  $("btnHelpFolder").addEventListener("click", () => api.open_folder());
  all("[data-link]").forEach((b) => b.addEventListener("click", async () =>
    showProblems($("aboutProblems"), (await api.open_link(b.dataset.link)).problems)));

  $("btnAck").addEventListener("click", () => { wiz.ack = !wiz.ack; renderWizard(); });
  $("btnWizHeal").addEventListener("click", () => beginCapture("wiz:heal"));
  $("btnWizBoost").addEventListener("click", () => beginCapture("wiz:boost"));
  $("btnWizBack").addEventListener("click", () => { if (wiz.step > 1) { wiz.step -= 1; renderWizard(); } });
  $("btnWizNext").addEventListener("click", wizardNext);

  document.addEventListener("keydown", handleCapture, true);
  document.addEventListener("mousedown", handleMouseCapture, true);
  ["mouseup", "click", "auxclick", "contextmenu"].forEach((type) =>
    document.addEventListener(type, swallowFollowUp, true));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
}

async function boot() {
  try {
    api = window.pywebview.api;
    const state = await api.get_state();
    config = state.config;
    env = state.env;
    applyTheme();
    $("brandVersion").textContent = state.version;
    $("aboutVersion").textContent = state.version;
    wire();
    if (env.needs_setup) { enterWizard(); } else { await enterMain(); }
    reveal();
  } catch (err) {
    /* A blank window with no explanation is the worst possible failure, so
       whatever went wrong is shown in the window itself rather than in a
       console nobody has open. */
    const box = $("bootError");
    box.textContent = "The window could not start: " + (err && err.message ? err.message : err);
    box.hidden = false;
    reveal();
  }
}

/* Tell Python the window is worth showing.
 *
 * Explicitly NOT requestAnimationFrame: the window is created hidden, and a
 * hidden window is never composited, so rAF simply never fires and the reveal
 * would always fall through to the 4 s watchdog -- which is worse than not
 * hiding it at all.
 *
 * `document.fonts.ready` is the right signal anyway. It does not depend on a
 * paint, and without it the interface assembles itself in stages: text drawn
 * in the fallback face, then reflowed when the real faces arrive. Waiting for it means the first frame the user ever sees is the
 * finished one. The race is a guarantee that a font that never loads cannot
 * hold the window shut. */
function reveal() {
  /* WebView2 hands focus to the first focusable element when the page loads,
     which would draw a focus ring around the minimize button on every launch,
     as if the window had opened with Minimize selected. */
  if (document.activeElement && document.activeElement !== document.body) {
    document.activeElement.blur();
  }
  const done = () => { try { api.ready(); } catch (err) { /* watchdog covers it */ } };
  if (document.fonts && document.fonts.ready) {
    Promise.race([
      document.fonts.ready,
      new Promise((resolve) => setTimeout(resolve, 1500)),
    ]).then(done, done);
  } else {
    done();
  }
}

/* pywebview injects its API and fires `pywebviewready` as soon as the DOM is
   ready, which can be *before* this file has finished executing. Listening only
   for the event therefore loses the race on a fast machine and leaves a blank
   window, so the already-ready case is handled first. */
if (window.pywebview && window.pywebview.api) {
  boot();
} else {
  window.addEventListener("pywebviewready", boot, { once: true });
}
