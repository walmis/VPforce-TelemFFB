// VPforce TelemFFB - in-sim settings panel.
//
// Talks to telemffb/api_server.py over plain HTTP (GET/POST JSON). That
// server only runs while MSFS is the connected sim, so most of the logic
// here is: poll for connectivity, render whatever it currently offers, and
// don't fight the user while they're mid-drag on a control.

// Stamped from manifest.json's package_version by build_layout.py.
const PANEL_VERSION = "0.3.1";
const API_BASE = "http://127.0.0.1:9873";
const STATUS_POLL_MS = 2000;
const SETTINGS_POLL_MS = 3000;
const FETCH_TIMEOUT_MS = 5000;
const LOG_PREFIX = "[VPFORCE-PANEL]";
// Requested order had 175 before 150 - reordered to ascending so a +/- stepper
// actually moves monotonically in one direction.
const SCALE_STEPS = [100, 125, 150, 175, 200, 250, 300, 350, 400, 450, 500];

function log(...args) {
    console.log(LOG_PREFIX, ...args);
}

const state = {
    connected: false,
    settings: [],       // last-known list from the server
};

function apiGet(path) {
    let timer = null;
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    if (controller) {
        timer = setTimeout(() => {
            log("timing out request:", path);
            controller.abort();
        }, FETCH_TIMEOUT_MS);
    }
    const opts = { cache: "no-store" };
    if (controller) opts.signal = controller.signal;

    // Coherent GT's JS engine doesn't implement Promise.prototype.finally
    // (ES2018) - use the two-argument .then(onSuccess, onError) form
    // everywhere instead, which is supported since ES2015.
    return fetch(API_BASE + path, opts).then(
        (r) => {
            if (timer) clearTimeout(timer);
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        },
        (err) => {
            if (timer) clearTimeout(timer);
            throw err;
        }
    );
}

function apiPost(name, value, unit) {
    return fetch(API_BASE + "/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, value: value, unit: unit || "" }),
    }).then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
    });
}

const DEVICE_LABELS = {
    joystick: "Joystick",
    pedals: "Pedals",
    collective: "Collective",
    trimwheel: "Trim Wheel",
};

function setConnected(connected, statusPayload) {
    state.connected = connected;
    document.body.classList.toggle("disconnected", !connected);
    const sub = document.getElementById("headerSub");
    const devices = (connected && statusPayload && statusPayload.devices) || [];
    if (connected && statusPayload) {
        const name = statusPayload.pattern || statusPayload.aircraft || "?";
        // with more than one device, the device tabs say which one is shown
        sub.textContent = statusPayload.device && devices.length < 2
            ? name + " - " + statusPayload.device : name;
    } else {
        sub.textContent = "Not connected";
    }
    renderDevices(devices, statusPayload ? statusPayload.device : null);
}

// One tab per device this TelemFFB drives (its own and its children's);
// the settings below are the selected device's.
function renderDevices(devices, current) {
    const root = document.getElementById("deviceTabs");
    const shown = devices.length > 1 ? devices.join(",") + "|" + current : "";
    if (root.dataset.shown === shown) return;
    root.dataset.shown = shown;
    root.innerHTML = "";
    root.style.display = shown ? "flex" : "none";
    if (!shown) return;
    for (const device of devices) {
        const pill = document.createElement("button");
        pill.className = "pill" + (device === current ? " selected" : "");
        pill.textContent = DEVICE_LABELS[device] || device;
        pill.addEventListener("click", () => {
            pill.blur();
            if (device !== current) selectDevice(device);
        });
        root.appendChild(pill);
    }
}

function selectDevice(device) {
    fetch(API_BASE + "/api/device", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: device }),
    }).then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
    }).then(
        (s) => {
            setConnected(!!s.connected, s);
            refreshSettingsNow();
        },
        (err) => {
            log("device switch failed:", err && err.message);
        }
    );
}

// The configuration errors TelemFFB is showing. TelemFFB holds each one
// until it has been gone for a few seconds, so this mirrors its list
// rather than keeping any of its own.
function renderFlagErrors(messages) {
    const root = document.getElementById("flagErrors");
    const text = messages.join("\n\n");
    if (root.dataset.shown === text) return;
    root.dataset.shown = text;
    root.innerHTML = "";
    for (const message of messages) {
        const el = document.createElement("div");
        el.className = "errorBanner";
        el.textContent = message;
        root.appendChild(el);
    }
}

function pollStatus() {
    apiGet("/api/status").then(
        (s) => {
            setConnected(!!s.connected, s);
            renderFlagErrors(s.connected && s.errors ? s.errors : []);
            setTimeout(pollStatus, STATUS_POLL_MS);
        },
        (err) => {
            log("status poll failed:", err && err.message);
            setConnected(false, null);
            renderFlagErrors([]);
            setTimeout(pollStatus, STATUS_POLL_MS);
        }
    );
}

function pollSettings() {
    log("fetching /api/settings...");
    apiGet("/api/settings").then(
        (data) => {
            log("got settings response, count =", (data.settings || []).length);
            state.settings = data.settings || [];
            renderSettings(state.settings);
            setTimeout(pollSettings, SETTINGS_POLL_MS);
        },
        (err) => {
            log("settings poll failed:", err && err.message);
            showError("Couldn't load settings: " + (err && err.message));
            setTimeout(pollSettings, SETTINGS_POLL_MS);
        }
    );
}

function showError(message) {
    const root = document.getElementById("settingsList");
    root.innerHTML = "";
    const el = document.createElement("div");
    el.className = "errorBanner";
    el.textContent = message;
    root.appendChild(el);
}

function groupKey(item) {
    return item.grouping || "Settings";
}

function orderValue(v) {
    const n = parseFloat(v);
    return isNaN(n) ? Number.MAX_SAFE_INTEGER : n;
}

// Desktop's SettingsLayout.py renders a setting whose 'order' ends in '1'
// with a '.' (e.g. "10700.1") inline on its 'prereq' parent's own row,
// sharing the parent's label, instead of as its own row - a very common
// "[Enable X] --- [X strength]" one-line pattern. Mirror that here: pull
// those items out of the flat list and key them by parent name, so
// renderRow can render both controls together.
function isBumpOrder(order) {
    return typeof order === "string" && order.length > 0 &&
        order[order.length - 1] === "1" && order.indexOf(".") !== -1;
}

// A ".2"+ decimal (as opposed to ".0"/bare-integer top-level, or ".1" bump
// children merged into their parent's row above) is an ordinary nested
// child in desktop's hierarchy - indent it one character width so it still
// reads as subordinate to whatever precedes it, without going as far as
// desktop's full per-depth indent.
function decimalDigit(order) {
    const dot = (order || "").indexOf(".");
    if (dot === -1) return -1;
    const n = parseInt(order.slice(dot + 1), 10);
    return isNaN(n) ? -1 : n;
}

function isIndentedChild(order) {
    return decimalDigit(order) >= 2;
}

function extractBumpChildren(settings) {
    const byName = new Map();
    for (const item of settings) byName.set(item.name, item);

    const bumpChildren = new Map(); // parent name -> child item
    const mainItems = [];
    for (const item of settings) {
        const isBump = isBumpOrder(item.order) && item.prereq && byName.has(item.prereq);
        // Only the first bump child claims a given parent row - a second one
        // (shouldn't happen in practice, but don't silently drop settings if
        // it does) just renders as its own ordinary row instead.
        if (isBump && !bumpChildren.has(item.prereq)) {
            bumpChildren.set(item.prereq, item);
        } else {
            mainItems.push(item);
        }
    }
    return { mainItems, bumpChildren };
}

function renderSettings(settings) {
    const root = document.getElementById("settingsList");

    if (!settings.length) {
        root.innerHTML = "";
        const el = document.createElement("div");
        el.className = "errorBanner";
        el.textContent = "Connected, but the server has no editable settings for this aircraft right now.";
        root.appendChild(el);
        return;
    }

    try {
        // Don't blow away a control the user is currently touching.
        const active = document.activeElement;
        const activeName = active && active.dataset ? active.dataset.name : null;

        const { mainItems, bumpChildren } = extractBumpChildren(settings);

        const groups = new Map();
        for (const item of mainItems) {
            const key = groupKey(item);
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(item);
        }
        const groupNames = Array.from(groups.keys()).sort((a, b) => {
            const ao = orderValue(groups.get(a)[0].order);
            const bo = orderValue(groups.get(b)[0].order);
            return ao - bo;
        });

        const frag = document.createDocumentFragment();
        for (const groupName of groupNames) {
            const items = groups.get(groupName).sort((a, b) => orderValue(a.order) - orderValue(b.order));

            const groupEl = document.createElement("div");
            groupEl.className = "group";

            const title = document.createElement("div");
            title.className = "groupTitle";
            title.textContent = groupName;
            groupEl.appendChild(title);

            for (const item of items) {
                const bumpChild = bumpChildren.get(item.name) || null;
                // skip re-render of a row being actively edited, whether that's
                // the row's own control or its inline bump-child control
                if (item.name === activeName) continue;
                if (bumpChild && bumpChild.name === activeName) continue;
                groupEl.appendChild(renderRow(item, bumpChild));
            }

            frag.appendChild(groupEl);
        }
        root.innerHTML = "";
        root.appendChild(frag);
    } catch (err) {
        showError("Error rendering settings: " + err.message);
    }
}

function renderRow(item, bumpChild) {
    const row = document.createElement("div");
    let cls = item.control === "choice" ? "row row--choice" : "row";
    if (isIndentedChild(item.order)) cls += " row--indent";
    row.className = cls;

    const label = document.createElement("div");
    label.className = "rowLabel";
    const labelText = document.createElement("span");
    labelText.textContent = item.displayname;
    label.appendChild(labelText);
    if (item.info) label.title = item.info; // already plain text - server strips HTML
    // a choice row's pills sit under the label, so its erase ends the label line
    if (item.control === "choice") label.appendChild(renderErase(item, row));
    row.appendChild(label);

    const control = document.createElement("div");
    control.className = "rowControl";

    if (item.control === "bool") {
        control.appendChild(renderBool(item, row));
    } else if (item.control === "choice") {
        control.appendChild(renderChoice(item, row));
    } else if (item.control === "range") {
        control.appendChild(renderRange(item, row));
    } else if (item.control === "button") {
        control.appendChild(renderBind(item, row));
    }
    if (item.control !== "choice") control.appendChild(renderErase(item, row));

    if (bumpChild) {
        const bumpEl = renderBumpControl(bumpChild, row);
        if (bumpEl) {
            control.appendChild(bumpEl);
            control.appendChild(renderErase(bumpChild, row));
        }
    }

    row.appendChild(control);
    return row;
}

// The desktop form's erase button, right of the control it clears: shown on
// a setting the user has changed, and the default comes back. Every control
// keeps the space so the buttons line up down the panel.
function renderErase(item, row) {
    const btn = document.createElement("button");
    btn.className = "eraseBtn";
    const icon = document.createElement("img");
    icon.src = "erase.svg";
    icon.alt = "";
    btn.appendChild(icon);
    if (!item.erasable) {
        btn.style.visibility = "hidden";
        btn.disabled = true;
        return btn;
    }
    btn.title = "Reset to default";
    btn.addEventListener("click", () => {
        btn.blur();
        markUpdating(row, fetch(API_BASE + "/api/erase", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: item.name }),
        }).then((r) => {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        }));
    });
    return btn;
}

function renderBumpControl(item, row) {
    if (item.control === "bool") return renderBool(item, row);
    if (item.control === "choice") return renderChoice(item, row);
    if (item.control === "range") return renderRange(item, row);
    if (item.control === "button") return renderBind(item, row);
    return null;
}

function refreshSettingsNow() {
    // A write can change which settings the server even returns (a bump
    // child appearing/disappearing with its toggle, or any other prereq
    // relationship) - don't wait for the next SETTINGS_POLL_MS tick to
    // show that, which could be up to 3s of visibly stale state.
    apiGet("/api/settings").then(
        (data) => {
            state.settings = data.settings || [];
            renderSettings(state.settings);
        },
        (err) => {
            log("immediate settings refresh failed:", err && err.message);
        }
    );
}

function markUpdating(row, promise) {
    row.classList.add("updating");
    promise.then(
        () => {
            row.classList.remove("updating");
            refreshSettingsNow();
        },
        (err) => {
            row.classList.remove("updating");
            log("write failed:", err && err.message);
            row.classList.add("writeFailed");
            setTimeout(() => row.classList.remove("writeFailed"), 2000);
        }
    );
}

// Button bindings.  Clicking asks TelemFFB to wait a few seconds for a
// button press on the device; the row counts down until the capture
// settles.  The list re-renders every few seconds, so the capture in
// progress is kept here rather than in the row.
const bind = { name: null, remaining: 0 };

function bindLabel(item) {
    if (bind.name === item.name) return "Push a button... " + bind.remaining;
    return item.value ? "Button " + item.value : "Click to bind";
}

function renderBind(item, row) {
    const btn = document.createElement("button");
    btn.className = "pill bindButton" + (bind.name === item.name ? " selected" : "");
    btn.dataset.name = item.name;
    btn.textContent = bindLabel(item);
    btn.addEventListener("click", () => {
        btn.blur();
        if (bind.name) return;   // one capture at a time
        fetch(API_BASE + "/api/bind", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: item.name }),
        }).then((r) => {
            if (!r.ok) throw new Error("HTTP " + r.status);
            bind.name = item.name;
            bind.remaining = 5;
            btn.classList.add("selected");
            btn.textContent = bindLabel(item);
            pollBind();
        }).then(null, (err) => {
            log("bind failed:", err && err.message);
            row.classList.add("writeFailed");
            setTimeout(() => row.classList.remove("writeFailed"), 2000);
        });
    });
    return btn;
}

function pollBind() {
    apiGet("/api/bind").then(
        (s) => {
            if (s.state === "waiting") {
                bind.remaining = s.remaining;
                const el = document.querySelector('.bindButton[data-name="' + bind.name + '"]');
                if (el) el.textContent = "Push a button... " + bind.remaining;
                setTimeout(pollBind, 250);
                return;
            }
            bind.name = null;
            refreshSettingsNow();
        },
        (err) => {
            log("bind status failed:", err && err.message);
            bind.name = null;
            refreshSettingsNow();
        }
    );
}

function renderBool(item, row) {
    const btn = document.createElement("button");
    btn.className = "toggle" + (item.value ? " on" : "");
    btn.dataset.name = item.name;
    const knob = document.createElement("div");
    knob.className = "knob";
    btn.appendChild(knob);

    btn.addEventListener("click", () => {
        const newVal = !btn.classList.contains("on");
        btn.classList.toggle("on", newVal);
        // A clicked <button> keeps DOM focus, and renderSettings() skips
        // rebuilding whatever row is focused (so an in-progress edit isn't
        // clobbered) - a toggle click is instantaneous, not an in-progress
        // edit, so blur it immediately or its own row would be excluded
        // from the refreshSettingsNow() this triggers.
        btn.blur();
        markUpdating(row, apiPost(item.name, newVal, item.unit));
    });

    return btn;
}

function renderChoice(item, row) {
    const wrap = document.createElement("div");
    wrap.className = "choiceGroup";
    wrap.dataset.name = item.name;
    wrap.tabIndex = -1;

    for (const opt of item.options) {
        const pill = document.createElement("button");
        pill.className = "pill" + (opt.value === item.value ? " selected" : "");
        pill.textContent = opt.label;
        pill.addEventListener("click", () => {
            wrap.querySelectorAll(".pill").forEach((p) => p.classList.remove("selected"));
            pill.classList.add("selected");
            pill.blur();
            markUpdating(row, apiPost(item.name, opt.value, item.unit));
        });
        wrap.appendChild(pill);
    }
    return wrap;
}

function renderRange(item, row) {
    // A plain <input type=range> styled via -webkit-appearance/pseudo-elements
    // renders much taller than the CSS asks for in Coherent GT (the same
    // engine that silently ignores Promise.prototype.finally) - built as
    // plain divs instead, same approach as the toggle, for full height control.
    const wrap = document.createElement("div");
    wrap.className = "rangeControl";

    const track = document.createElement("div");
    track.className = "sliderTrack";
    track.dataset.name = item.name;

    const fill = document.createElement("div");
    fill.className = "sliderFill";
    const thumb = document.createElement("div");
    thumb.className = "sliderThumb";
    track.appendChild(fill);
    track.appendChild(thumb);

    const valueLabel = document.createElement("div");
    valueLabel.className = "rangeValue";

    const min = item.min;
    const max = item.max;
    const step = item.step || 0.01;
    let value = item.value;

    function clamp01(p) {
        if (p < 0) return 0;
        if (p > 1) return 1;
        return p;
    }

    function snap(v) {
        let stepped = Math.round((v - min) / step) * step + min;
        if (stepped < min) stepped = min;
        if (stepped > max) stepped = max;
        return Math.round(stepped * 1e6) / 1e6; // shake off float noise
    }

    function paint(v) {
        const pct = max === min ? 0 : clamp01((v - min) / (max - min)) * 100;
        fill.style.width = pct + "%";
        thumb.style.left = pct + "%";
        valueLabel.textContent = formatRangeValue(v, item.display, item.unit);
    }

    function commit() {
        markUpdating(row, apiPost(item.name, value, item.unit));
    }

    // Driven by mouse movement *relative to where the drag started*, not by
    // mapping clientX onto the track's absolute getBoundingClientRect()
    // position. Coherent GT's CSS 'zoom' (used for the panel-wide scale
    // control) doesn't seem to keep clientX and getBoundingClientRect() in
    // the same coordinate space the way a real browser does - at a
    // non-100% zoom level, that mismatch made every click resolve to
    // ~100%. Relative movement only ever compares clientX against itself,
    // so it can't be thrown off by that disagreement.
    let dragging = false;
    let dragStartClientX = 0;
    let dragStartValue = 0;
    let dragTrackWidth = 0;

    function onMove(e) {
        if (!dragging || !dragTrackWidth) return;
        const deltaPct = (e.clientX - dragStartClientX) / dragTrackWidth;
        value = snap(dragStartValue + deltaPct * (max - min));
        paint(value);
    }
    function onUp() {
        if (!dragging) return;
        dragging = false;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        commit();
    }
    track.addEventListener("mousedown", (e) => {
        dragging = true;
        dragStartClientX = e.clientX;
        dragStartValue = value;
        dragTrackWidth = track.getBoundingClientRect().width || track.offsetWidth || 1;
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
        e.preventDefault();
    });

    paint(value);
    wrap.appendChild(track);
    wrap.appendChild(valueLabel);
    return wrap;
}

function formatRangeValue(v, display, unit) {
    if (display === "percent") return (v * 100).toFixed(1) + "%";
    if (Number.isInteger(v)) return v.toFixed(0) + (unit || "");
    return v.toFixed(2) + (unit || "");
}

function postPanelZoom(zoom) {
    return fetch(API_BASE + "/api/panel-zoom", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zoom: zoom }),
    }).then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
    });
}

function initScaleControl() {
    const label = document.getElementById("scaleLabel");
    let index = 0; // SCALE_STEPS[0] === 100%, until the persisted value loads

    function apply(persist) {
        const pct = SCALE_STEPS[index];
        label.textContent = pct + "%";
        // 'zoom' (not transform: scale) so layout/scroll extent actually
        // reflow to match - transform would need manual size compensation
        // to avoid clipping or dead scroll space. Applied to the whole page
        // (not just #settingsList) so the header scales along with it.
        document.body.style.zoom = pct / 100;
        if (persist) {
            // Server picks msfs_panel_zoom vs msfs_panel_zoom_vr based on the
            // current CameraState - the client doesn't need to know which.
            postPanelZoom(pct).then(null, (err) => log("panel-zoom save failed:", err && err.message));
        }
    }

    document.getElementById("scaleDown").addEventListener("click", () => {
        if (index > 0) {
            index -= 1;
            apply(true);
        }
    });
    document.getElementById("scaleUp").addEventListener("click", () => {
        if (index < SCALE_STEPS.length - 1) {
            index += 1;
            apply(true);
        }
    });

    // Load the persisted zoom once at startup and apply it without
    // immediately re-saving the same value back.
    apiGet("/api/panel-zoom").then(
        (data) => {
            const idx = SCALE_STEPS.indexOf(data.zoom);
            index = idx >= 0 ? idx : 0;
            apply(false);
        },
        (err) => {
            log("panel-zoom load failed:", err && err.message);
            apply(false);
        }
    );
}

// --- Monitor -------------------------------------------------------------
// The desktop Monitor tab: active effects and telemetry for the selected
// device. Polled only while shown; rows are updated in place and rebuilt
// only when the set of keys changes, since rebuilding a hundred rows several
// times a second is costly on the sim's UI thread.

const MONITOR_POLL_MS = 300;
const monitor = { view: "settings", favoritesOnly: true, generation: 0 };
const listKeys = {};    // list element id -> the keys it last showed, joined
const listRows = {};    // list element id -> { key: row element }

function setView(name) {
    monitor.view = name;
    monitor.generation += 1;
    document.getElementById("settingsList").style.display = name === "settings" ? "block" : "none";
    document.getElementById("monitorView").style.display = name === "monitor" ? "block" : "none";
    document.querySelectorAll("#viewTabs .pill").forEach((p) =>
        p.classList.toggle("selected", p.dataset.view === name));
    if (name === "monitor") pollMonitor(monitor.generation);
}

function setMonitorFilter(filter) {
    monitor.favoritesOnly = filter === "favorites";
    document.querySelectorAll("#monitorBar .pill").forEach((p) =>
        p.classList.toggle("selected", p.dataset.filter === filter));
    listKeys.telemList = null;          // rebuild on the next poll
}

function pollMonitor(generation) {
    if (generation !== monitor.generation) return;
    apiGet("/api/monitor").then(
        (data) => { renderMonitor(data); },
        (err) => { log("monitor poll failed:", err && err.message); }
    ).then(() => {
        if (generation === monitor.generation) {
            setTimeout(() => pollMonitor(generation), MONITOR_POLL_MS);
        }
    });
}

// Show ``items`` in the list ``id``: rebuilt through ``build`` when its keys
// change, otherwise each row brought up to date through ``update``.
function syncList(id, items, keyOf, build, update, emptyText) {
    const root = document.getElementById(id);
    const keys = items.map(keyOf).join("\u0001");
    if (listKeys[id] !== keys) {
        listKeys[id] = keys;
        listRows[id] = {};
        root.innerHTML = "";
        if (!items.length) {
            const hint = document.createElement("div");
            hint.className = "monitorHint";
            hint.textContent = emptyText;
            root.appendChild(hint);
        }
        for (const item of items) {
            const row = build(item);
            listRows[id][keyOf(item)] = row;
            root.appendChild(row);
        }
    }
    for (const item of items) update(listRows[id][keyOf(item)], item);
}

function setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
}

function buildEffectRow(effect) {
    const row = document.createElement("div");
    row.className = "effectRow";
    // the desktop's badge: the waveform for a periodic, a letter otherwise,
    // in a box every row keeps so the labels line up
    const type = document.createElement("span");
    type.className = "effectBadge";
    type.title = effect.type;
    if (effect.shape) {
        const glyph = document.createElement("img");
        glyph.src = effect.shape;
        glyph.alt = "";
        type.appendChild(glyph);
    } else if (effect.letter) {
        type.textContent = effect.letter;
    }
    const label = document.createElement("span");
    label.className = "effectLabel";
    label.textContent = effect.label;
    const bar = document.createElement("div");
    bar.className = "effectBar";
    const fill = document.createElement("div");
    fill.className = "effectFill";
    bar.appendChild(fill);
    const value = document.createElement("span");
    value.className = "effectValue";
    row.appendChild(type);
    row.appendChild(label);
    row.appendChild(bar);
    row.appendChild(value);
    return row;
}

function updateEffectRow(row, effect) {
    const bar = row.children[2];
    const shown = effect.intensity === null || effect.intensity === undefined ? "hidden" : "visible";
    if (bar.style.visibility !== shown) bar.style.visibility = shown;
    if (shown === "visible") {
        const width = Math.max(0, Math.min(100, effect.intensity * 100)).toFixed(0) + "%";
        if (bar.firstChild.style.width !== width) bar.firstChild.style.width = width;
    }
    setText(row.children[3], effect.shown);
}

function buildTelemRow(item) {
    const row = document.createElement("div");
    row.className = "telemRow";
    const star = document.createElement("img");
    star.className = "star";
    star.alt = "";
    star.addEventListener("click", () => toggleFavorite(item.key, star));
    const key = document.createElement("span");
    key.className = "telemKey";
    key.textContent = item.key;
    const value = document.createElement("span");
    value.className = "telemValue";
    row.appendChild(star);
    row.appendChild(key);
    row.appendChild(value);
    return row;
}

function updateTelemRow(row, item) {
    const src = item.favorite ? "star_on.svg" : "star_off.svg";
    if (row.dataset.star !== src) {
        row.dataset.star = src;
        row.firstChild.src = src;
    }
    setText(row.children[2], item.value);
}

function toggleFavorite(key, star) {
    fetch(API_BASE + "/api/monitor/favorite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: key }),
    }).then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
    }).then(
        (res) => {
            const src = res.favorite ? "star_on.svg" : "star_off.svg";
            star.parentNode.dataset.star = src;
            star.src = src;
            if (monitor.favoritesOnly) listKeys.telemList = null;
        },
        (err) => { log("favorite failed:", err && err.message); }
    );
}

function renderMonitor(data) {
    syncList("effectsList", data.effects || [], (e) => e.label,
        buildEffectRow, updateEffectRow, "No effects are active.");
    const all = data.telemetry || [];
    const rows = monitor.favoritesOnly ? all.filter((r) => r.favorite) : all;
    const empty = !all.length ? "Waiting for telemetry..."
        : "No favorites yet: show All and tap a star to add one.";
    syncList("telemList", rows, (r) => r.key, buildTelemRow, updateTelemRow, empty);
}

document.querySelectorAll("#viewTabs .pill").forEach((p) =>
    p.addEventListener("click", () => { p.blur(); setView(p.dataset.view); }));
document.querySelectorAll("#monitorBar .pill").forEach((p) =>
    p.addEventListener("click", () => { p.blur(); setMonitorFilter(p.dataset.filter); }));

window.addEventListener("error", (e) => {
    log("uncaught error:", e.message, "at", e.filename + ":" + e.lineno);
    showError("Script error: " + e.message);
});
window.addEventListener("unhandledrejection", (e) => {
    const msg = e.reason && e.reason.message ? e.reason.message : String(e.reason);
    log("unhandled promise rejection:", msg);
    showError("Unhandled error: " + msg);
});

log("panel.js loaded, starting poll loops");
document.getElementById("settingsList").textContent = "Loading...";
document.getElementById("panelVersion").textContent = "v" + PANEL_VERSION;

initScaleControl();
pollStatus();
pollSettings();
