// VPforce TelemFFB - in-sim settings panel.
//
// Talks to telemffb/api_server.py over plain HTTP (GET/POST JSON). That
// server only runs while MSFS is the connected sim, so most of the logic
// here is: poll for connectivity, render whatever it currently offers, and
// don't fight the user while they're mid-drag on a control.

const API_BASE = "http://127.0.0.1:9010";
const STATUS_POLL_MS = 2000;
const SETTINGS_POLL_MS = 3000;
const RANGE_COMMIT_DEBOUNCE_MS = 400;

const state = {
    connected: false,
    settings: [],       // last-known list from the server
    pendingRangeTimers: new Map(),  // name -> setTimeout id
};

function apiGet(path) {
    return fetch(API_BASE + path, { cache: "no-store" }).then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
    });
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
        sub.textContent = (statusPayload.aircraft || "?") + "  •  " + (statusPayload.class || "");
    } else {
        sub.textContent = "Not connected";
    }
}

function pollStatus() {
    apiGet("/api/status")
        .then((s) => {
            setConnected(!!s.connected, s);
        })
        .catch(() => {
            setConnected(false, null);
        })
        .finally(() => {
            setTimeout(pollStatus, STATUS_POLL_MS);
        });
}

function pollSettings() {
    if (state.connected) {
        apiGet("/api/settings")
            .then((data) => {
                state.settings = data.settings || [];
                renderSettings(state.settings);
            })
            .catch(() => {
                /* leave last-rendered list in place; status poll will flag disconnect */
            })
            .finally(() => {
                setTimeout(pollSettings, SETTINGS_POLL_MS);
            });
    } else {
        setTimeout(pollSettings, SETTINGS_POLL_MS);
    }
}

function groupKey(item) {
    return item.grouping || "Settings";
}

function orderValue(v) {
    const n = parseFloat(v);
    return isNaN(n) ? Number.MAX_SAFE_INTEGER : n;
}

function renderSettings(settings) {
    const root = document.getElementById("settingsList");

    // Don't blow away a control the user is currently touching.
    const active = document.activeElement;
    const activeName = active && active.dataset ? active.dataset.name : null;

    const groups = new Map();
    for (const item of settings) {
        const key = groupKey(item);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
    }
    const groupNames = Array.from(groups.keys()).sort((a, b) => {
        const ao = orderValue(groups.get(a)[0].order);
        const bo = orderValue(groups.get(b)[0].order);
        return ao - bo;
    });

    root.innerHTML = "";
    for (const groupName of groupNames) {
        const items = groups.get(groupName).sort((a, b) => orderValue(a.order) - orderValue(b.order));

        const groupEl = document.createElement("div");
        groupEl.className = "group";

        const title = document.createElement("div");
        title.className = "groupTitle";
        title.textContent = groupName;
        groupEl.appendChild(title);

        for (const item of items) {
            if (item.name === activeName) continue; // skip re-render of the row being edited
            groupEl.appendChild(renderRow(item));
        }

        root.appendChild(groupEl);
    }
}

function renderRow(item) {
    const row = document.createElement("div");
    row.className = "row";

    const label = document.createElement("div");
    label.className = "rowLabel";
    label.textContent = item.displayname;
    if (item.info) label.title = stripHtml(item.info);
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

    row.appendChild(control);
    return row;
}

function stripHtml(s) {
    const div = document.createElement("div");
    div.innerHTML = s;
    return div.textContent || "";
}

function markUpdating(row, promise) {
    row.classList.add("updating");
    promise.finally(() => row.classList.remove("updating"));
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
            markUpdating(row, apiPost(item.name, opt.value, item.unit));
        });
        wrap.appendChild(pill);
    }
    return wrap;
}

function renderRange(item, row) {
    const wrap = document.createElement("div");
    wrap.className = "rangeControl";

    const input = document.createElement("input");
    input.type = "range";
    input.min = item.min;
    input.max = item.max;
    input.step = item.step || 0.01;
    input.value = item.value;
    input.dataset.name = item.name;

    const valueLabel = document.createElement("div");
    valueLabel.className = "rangeValue";
    valueLabel.textContent = formatRangeValue(item.value, item.display, item.unit);

    function commit() {
        const v = parseFloat(input.value);
        valueLabel.textContent = formatRangeValue(v, item.display, item.unit);
        markUpdating(row, apiPost(item.name, v, item.unit));
    }

    input.addEventListener("input", () => {
        valueLabel.textContent = formatRangeValue(parseFloat(input.value), item.display, item.unit);
        const timers = state.pendingRangeTimers;
        if (timers.has(item.name)) clearTimeout(timers.get(item.name));
        timers.set(item.name, setTimeout(commit, RANGE_COMMIT_DEBOUNCE_MS));
    });
    input.addEventListener("change", () => {
        const timers = state.pendingRangeTimers;
        if (timers.has(item.name)) {
            clearTimeout(timers.get(item.name));
            timers.delete(item.name);
        }
        commit();
    });

    wrap.appendChild(input);
    wrap.appendChild(valueLabel);
    return wrap;
}

function formatRangeValue(v, display, unit) {
    if (display === "percent") return (v * 100).toFixed(1) + "%";
    if (Number.isInteger(v)) return v.toFixed(0) + (unit || "");
    return v.toFixed(2) + (unit || "");
}

pollStatus();
pollSettings();
