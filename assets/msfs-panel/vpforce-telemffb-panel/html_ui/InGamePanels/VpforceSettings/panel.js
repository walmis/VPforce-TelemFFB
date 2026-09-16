// VPforce TelemFFB - in-sim settings panel.
//
// Talks to telemffb/api_server.py over plain HTTP (GET/POST JSON). That
// server only runs while MSFS is the connected sim, so most of the logic
// here is: poll for connectivity, render whatever it currently offers, and
// don't fight the user while they're mid-drag on a control.

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

function setConnected(connected, statusPayload) {
    state.connected = connected;
    document.body.classList.toggle("disconnected", !connected);
    const sub = document.getElementById("headerSub");
    if (connected && statusPayload) {
        const name = statusPayload.pattern || statusPayload.aircraft || "?";
        sub.textContent = statusPayload.device ? name + " - " + statusPayload.device : name;
    } else {
        sub.textContent = "Not connected";
    }
}

function pollStatus() {
    apiGet("/api/status").then(
        (s) => {
            setConnected(!!s.connected, s);
            setTimeout(pollStatus, STATUS_POLL_MS);
        },
        (err) => {
            log("status poll failed:", err && err.message);
            setConnected(false, null);
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
    label.textContent = item.displayname;
    if (item.info) label.title = item.info; // already plain text - server strips HTML
    row.appendChild(label);

    const control = document.createElement("div");
    control.className = "rowControl";

    if (item.control === "bool") {
        control.appendChild(renderBool(item, row));
    } else if (item.control === "choice") {
        control.appendChild(renderChoice(item, row));
    } else if (item.control === "range") {
        control.appendChild(renderRange(item, row));
    }

    if (bumpChild) {
        const bumpEl = renderBumpControl(bumpChild, row);
        if (bumpEl) control.appendChild(bumpEl);
    }

    row.appendChild(control);
    return row;
}

function renderBumpControl(item, row) {
    if (item.control === "bool") return renderBool(item, row);
    if (item.control === "choice") return renderChoice(item, row);
    if (item.control === "range") return renderRange(item, row);
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

initScaleControl();
pollStatus();
pollSettings();
