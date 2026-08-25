const button = document.getElementById("uploadButton");
const previewButton = document.getElementById("previewButton");
const closePreviewButton = document.getElementById("closePreviewButton");
const themeToggle = document.getElementById("themeToggle");
const themeToggleLabel = document.getElementById("themeToggleLabel");
const MAX_FILE_SIZE = 10 * 1024 * 1024;
const API_BASE_URL = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
let resultUrl;
let referenceSpectra = {};
let referenceProfiles = [];
let referenceSpectraSource = "valid.csv";
let authToken = null;
let currentUser = null;
let employees = [];
let todoItems = [];
let todoFilter = "all";
let todoEditorContext = null;
let activeModelName = "model2";
let diagnosticNavigator = null;
let diagnosticPredictions = [];
let diagnosticSourceRows = [];

function apiUrl(path) {
    return `${API_BASE_URL}${path}`;
}

function storedTheme() {
    try {
        return localStorage.getItem("piher2-theme");
    } catch {
        return null;
    }
}

function applyTheme(theme) {
    const dark = theme === "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    themeToggle.setAttribute("aria-pressed", String(dark));
    themeToggle.querySelector(".theme-toggle-icon").textContent = dark ? "☀" : "☾";
    themeToggleLabel.textContent = dark ? "Tryb jasny" : "Tryb ciemny";
    const selectedCylinder = document.querySelector(".cylinder.selected");
    if (selectedCylinder) {
        selectedCylinder.dataset.preventAutoScroll = "true";
        selectedCylinder.click();
        selectedCylinder.click();
        delete selectedCylinder.dataset.preventAutoScroll;
    }
}

applyTheme(storedTheme() || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
themeToggle.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
    try {
        localStorage.setItem("piher2-theme", nextTheme);
    } catch {
        // Tryb nadal działa w bieżącej karcie, nawet gdy zapis jest zablokowany.
    }
});

function createInteractiveWallpaper() {
    const canvas = document.getElementById("interactiveWallpaper");
    if (!canvas) return;

    const context = canvas.getContext("2d");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const pointer = { x: -1000, y: -1000, active: false };
    let particles = [];
    let frameId;

    function resize() {
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = window.innerWidth * ratio;
        canvas.height = window.innerHeight * ratio;
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        const count = Math.min(70, Math.max(26, Math.round(window.innerWidth / 22)));
        particles = Array.from({ length: count }, () => ({
            x: Math.random() * window.innerWidth,
            y: Math.random() * window.innerHeight,
            radius: 1.5 + Math.random() * 3.5,
            speedX: (Math.random() - 0.5) * 0.28,
            speedY: (Math.random() - 0.5) * 0.28,
            color: Math.random() > 0.5 ? "30, 169, 122" : "245, 158, 11"
        }));
        draw(true);
    }

    function draw(still = false) {
        context.clearRect(0, 0, window.innerWidth, window.innerHeight);

        particles.forEach((particle, index) => {
            particles.slice(index + 1).forEach(otherParticle => {
                const distance = Math.hypot(particle.x - otherParticle.x, particle.y - otherParticle.y);
                const connectionRange = 135;

                if (distance >= connectionRange) return;

                context.beginPath();
                context.moveTo(particle.x, particle.y);
                context.lineTo(otherParticle.x, otherParticle.y);
                context.strokeStyle = `rgba(22, 107, 77, ${0.22 * (1 - distance / connectionRange)})`;
                context.lineWidth = 1;
                context.stroke();
            });
        });

        particles.forEach(particle => {
            if (!still) {
                const offsetX = particle.x - pointer.x;
                const offsetY = particle.y - pointer.y;
                const cursorDistance = Math.hypot(offsetX, offsetY);
                const repulsionRange = 180;
                const repulsion = pointer.active && cursorDistance < repulsionRange
                    ? (1 - cursorDistance / repulsionRange) * 3.4
                    : 0;
                const directionX = cursorDistance ? offsetX / cursorDistance : 0;
                const directionY = cursorDistance ? offsetY / cursorDistance : 0;

                particle.x += particle.speedX + directionX * repulsion;
                particle.y += particle.speedY + directionY * repulsion;
                if (particle.x < -10 || particle.x > window.innerWidth + 10) particle.speedX *= -1;
                if (particle.y < -10 || particle.y > window.innerHeight + 10) particle.speedY *= -1;
            }

            const distance = Math.hypot(pointer.x - particle.x, pointer.y - particle.y);
            const influence = pointer.active ? Math.max(0, 1 - distance / 180) : 0;
            const radius = particle.radius + influence * 6;
            context.beginPath();
            context.arc(particle.x, particle.y, radius, 0, Math.PI * 2);
            context.fillStyle = `rgba(${particle.color}, ${0.28 + influence * 0.38})`;
            context.fill();
        });

        if (!still && !reducedMotion.matches) frameId = requestAnimationFrame(draw);
    }

    window.addEventListener("pointermove", event => {
        pointer.x = event.clientX;
        pointer.y = event.clientY;
        pointer.active = true;
    }, { passive: true });
    window.addEventListener("pointerleave", () => { pointer.active = false; });
    window.addEventListener("resize", resize, { passive: true });
    reducedMotion.addEventListener("change", () => {
        cancelAnimationFrame(frameId);
        draw(reducedMotion.matches);
    });

    resize();
    if (!reducedMotion.matches) draw();
}

createInteractiveWallpaper();

const labelNames = {
    ok: "sprawny",
    zakoksowany: "zakoksowany",
    lejacy: "lejący",
    pompa: "pompa",
    iglica: "iglica",
    unknown: "inna anomalia"
};

const severityNames = {
    male: "małe",
    srednie: "średnie",
    duze: "duże",
    nie_dotyczy: "nie dotyczy"
};

function referenceProfileKey(label, severity = null) {
    return `${label}:${severity || ""}`;
}

function findReferenceProfile(label, severity = null) {
    const exact = referenceProfiles.find(profile =>
        profile.label === label && (profile.severity || null) === (severity || null)
    );
    return exact || null;
}

function createReferenceDrawer(initialProfile, onProfileChange) {
    const drawer = document.createElement("details");
    const summary = document.createElement("summary");
    const body = document.createElement("div");
    const help = document.createElement("p");
    const tableWrap = document.createElement("div");
    const table = document.createElement("table");
    const caption = document.createElement("caption");
    const header = document.createElement("thead");
    const headerRow = document.createElement("tr");
    const tableBody = document.createElement("tbody");
    const severities = ["male", "srednie", "duze"];
    const faultLabels = ["iglica", "lejacy", "pompa", "zakoksowany"];
    let selectedKey = initialProfile
        ? referenceProfileKey(initialProfile.label, initialProfile.severity)
        : null;

    drawer.className = "reference-drawer";
    summary.textContent = "Średnie usterek";
    body.className = "reference-drawer-body";
    help.textContent = "Wybierz profil, aby nałożyć go na wykres. Kliknij ponownie, aby go wyłączyć.";
    tableWrap.className = "reference-table-wrap";
    table.className = "reference-table";
    caption.textContent = "Typ awarii i jej poważność";

    ["Typ awarii", "Mała", "Średnia", "Duża"].forEach(text => {
        const cell = document.createElement("th");
        cell.scope = "col";
        cell.textContent = text;
        headerRow.append(cell);
    });
    header.append(headerRow);

    const createProfileButton = (profile, text = "Nałóż") => {
        const profileKey = referenceProfileKey(profile.label, profile.severity);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "reference-profile-button";
        button.dataset.profileKey = profileKey;
        button.setAttribute("aria-pressed", String(profileKey === selectedKey));
        button.classList.toggle("active", profileKey === selectedKey);
        button.textContent = text;
        button.title = `Nałóż średnią: ${labelNames[profile.label] || profile.label}${
            profile.severity ? `, ${severityNames[profile.severity] || profile.severity}` : ""
        }`;
        button.addEventListener("click", () => {
            selectedKey = selectedKey === profileKey ? null : profileKey;
            table.querySelectorAll(".reference-profile-button").forEach(item => {
                const active = item.dataset.profileKey === selectedKey;
                item.classList.toggle("active", active);
                item.setAttribute("aria-pressed", String(active));
            });
            onProfileChange(selectedKey === profileKey ? profile : null);
        });
        return button;
    };

    faultLabels.forEach(label => {
        const row = document.createElement("tr");
        const labelCell = document.createElement("th");
        labelCell.scope = "row";
        labelCell.textContent = labelNames[label] || label;
        row.append(labelCell);
        severities.forEach(severity => {
            const cell = document.createElement("td");
            const profile = findReferenceProfile(label, severity);
            if (profile) cell.append(createProfileButton(profile));
            else cell.textContent = "—";
            row.append(cell);
        });
        tableBody.append(row);
    });

    const createStandaloneRow = (label, description, alwaysVisible = false) => {
        const row = document.createElement("tr");
        const labelCell = document.createElement("th");
        const valueCell = document.createElement("td");
        const profile = findReferenceProfile(label);
        labelCell.scope = "row";
        labelCell.textContent = labelNames[label] || label;
        valueCell.colSpan = severities.length;
        valueCell.className = "reference-standalone-cell";
        if (profile && alwaysVisible) {
            const marker = document.createElement("span");
            marker.className = "reference-always-visible";
            marker.textContent = description;
            valueCell.append(marker);
        } else if (profile) {
            valueCell.append(createProfileButton(profile, description));
        } else {
            valueCell.textContent = "—";
        }
        row.append(labelCell, valueCell);
        tableBody.append(row);
    };

    createStandaloneRow("ok", "bez poważności · zawsze na zielono", true);
    table.append(caption, header, tableBody);
    tableWrap.append(table);
    body.append(help, tableWrap);
    drawer.append(summary, body);
    return drawer;
}

function parseCsv(text) {
    const rows = [];
    let row = [], field = "", quoted = false;

    for (let index = 0; index < text.length; index += 1) {
        const char = text[index];
        if (char === '"') {
            if (quoted && text[index + 1] === '"') {
                field += char;
                index += 1;
            } else {
                quoted = !quoted;
            }
        } else if (char === "," && !quoted) {
            row.push(field);
            field = "";
        } else if ((char === "\n" || char === "\r") && !quoted) {
            if (char === "\r" && text[index + 1] === "\n") index += 1;
            row.push(field);
            if (row.some(value => value !== "")) rows.push(row);
            row = [];
            field = "";
        } else {
            field += char;
        }
    }
    if (field || row.length) rows.push([...row, field]);

    const [headers, ...values] = rows;
    return values.map(row => Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""])));
}

function rowsToCsv(rows) {
    if (!rows.length) return "";
    const headers = [...new Set(rows.flatMap(row => Object.keys(row)))];
    const escapeField = value => {
        const text = value == null ? "" : String(value);
        return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
    };
    return [
        headers.map(escapeField).join(","),
        ...rows.map(row => headers.map(header => escapeField(row[header])).join(","))
    ].join("\n");
}

function suspiciousMeasurementIndices(value) {
    return String(value || "")
        .split(",")
        .map(column => Number(column.trim().replace(/^mV_/, "")))
        .filter(index => Number.isInteger(index) && index >= 0 && index <= 20);
}

function countMissingMeasurements(source) {
    if (!source) return 0;
    return Array.from({ length: 21 }, (_, index) => Number(source[`mV_${index}`]))
        .filter(value => !Number.isFinite(value) || value === 0).length;
}

function cylinderStatusText(row) {
    if (row.label === "ok") return "Sprawny";
    if (row.label === "unknown") return "Inna anomalia";
    return `${labelNames[row.label] || row.label} · ${severityNames[row.severity] || row.severity} nasilenie`;
}

async function readSourceCsv(file) {
    if (file.name.toLowerCase().endsWith(".csv")) return file.text();

    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(apiUrl("/api/extract-csv"), {
        method: "POST",
        body: formData
    });
    if (!response.ok) {
        const error = await response.text();
        throw new Error(error || "Nie udało się odczytać pliku ZIP.");
    }
    return response.text();
}

function drawCylinderGraph(canvas, source, state, {
    width = 220,
    height = 48,
    showScale = false,
    averageValues = null,
    faultAverageValues = null,
    highlightedIndices = []
} = {}) {
    const values = Array.from({ length: 21 }, (_, index) => Number(source[`mV_${index}`]));
    const plottedValues = [
        ...values,
        ...(averageValues || []),
        ...(faultAverageValues || [])
    ].filter(value => Number.isFinite(value) && value !== 0);
    if (plottedValues.length < 2) return false;

    const colors = {
        ok: "#1ea97a",
        male: "#d99800",
        srednie: "#e37c00",
        duze: "#dc2626",
        unknown: "#7467dc"
    };
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const context = canvas.getContext("2d");
    const min = 0;
    const max = Math.max(...plottedValues);
    const span = max - min || 1;
    const padding = showScale
        ? { left: 52, right: 52, top: 14, bottom: 28 }
        : { left: 3, right: 3, top: 3, bottom: 3 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const color = colors[state] || colors.unknown;
    const darkTheme = document.documentElement.dataset.theme === "dark";
    const axisTextColor = darkTheme ? "rgba(222, 239, 232, 0.72)" : "rgba(31, 41, 55, 0.68)";

    canvas.width = width * ratio;
    canvas.height = height * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    if (showScale) {
        context.font = "13px Arial, sans-serif";
        context.lineWidth = 1;
        context.strokeStyle = "rgba(22, 107, 77, 0.12)";
        context.fillStyle = axisTextColor;
        context.textAlign = "right";
        context.textBaseline = "middle";

        [0, 0.5, 1].forEach(tick => {
            const y = padding.top + (1 - tick) * plotHeight;
            const value = min + tick * span;
            context.beginPath();
            context.moveTo(padding.left, y);
            context.lineTo(width - padding.right, y);
            context.stroke();
            context.fillText(value.toFixed(1), padding.left - 8, y);
        });

        context.textAlign = "center";
        context.textBaseline = "top";
        [0, 10, 20].forEach(index => {
            const x = padding.left + (index / (values.length - 1)) * plotWidth;
            context.fillText(`mV_${index}`, x, height - padding.bottom + 9);
        });
        context.textAlign = "left";
        context.textBaseline = "alphabetic";
        context.fillText("mV", padding.left, 9);
    }

    if (highlightedIndices.length) {
        const firstIndex = Math.min(...highlightedIndices);
        const lastIndex = Math.max(...highlightedIndices);
        const pointWidth = plotWidth / (values.length - 1);
        const startX = Math.max(padding.left, padding.left + firstIndex * pointWidth - pointWidth / 2);
        const endX = Math.min(
            width - padding.right,
            padding.left + lastIndex * pointWidth + pointWidth / 2
        );
        context.fillStyle = "rgba(239, 68, 68, 0.12)";
        context.fillRect(startX, padding.top, endX - startX, plotHeight);
        context.strokeStyle = "rgba(220, 38, 38, 0.48)";
        context.lineWidth = 1;
        context.strokeRect(startX, padding.top, endX - startX, plotHeight);
    }

    const drawLine = () => {
        let started = false;
        context.beginPath();
        values.forEach((value, index) => {
            if (!Number.isFinite(value) || value === 0) return;
            const x = padding.left + (index / (values.length - 1)) * plotWidth;
            const y = height - padding.bottom - ((value - min) / span) * plotHeight;
            if (started) context.lineTo(x, y);
            else {
                context.moveTo(x, y);
                started = true;
            }
        });
    };

    drawLine();
    const firstValidIndex = values.findIndex(value => Number.isFinite(value) && value !== 0);
    const lastValidIndex = values.length - 1 - [...values].reverse().findIndex(
        value => Number.isFinite(value) && value !== 0
    );
    const firstX = padding.left + (firstValidIndex / (values.length - 1)) * plotWidth;
    const lastX = padding.left + (lastValidIndex / (values.length - 1)) * plotWidth;
    context.lineTo(lastX, height - padding.bottom);
    context.lineTo(firstX, height - padding.bottom);
    context.closePath();
    const fill = context.createLinearGradient(0, 0, 0, height);
    fill.addColorStop(0, `${color}33`);
    fill.addColorStop(1, `${color}00`);
    context.fillStyle = fill;
    context.fill();

    drawLine();
    context.strokeStyle = color;
    context.lineWidth = 1.8;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.stroke();

    const drawReferenceLine = (referenceValues, strokeStyle, dash) => {
        if (!referenceValues) return;
        context.beginPath();
        let started = false;
        referenceValues.forEach((value, index) => {
            if (!Number.isFinite(value) || value === 0) return;
            const x = padding.left + (index / (referenceValues.length - 1)) * plotWidth;
            const y = height - padding.bottom - ((value - min) / span) * plotHeight;
            if (started) context.lineTo(x, y);
            else {
                context.moveTo(x, y);
                started = true;
            }
        });
        context.setLineDash(dash);
        context.strokeStyle = strokeStyle;
        context.lineWidth = showScale ? 2 : 1.2;
        context.stroke();
        context.setLineDash([]);
    };
    drawReferenceLine(averageValues, "#16865f", [7, 5]);
    drawReferenceLine(faultAverageValues, "#d97706", [3, 4]);
    return true;
}

function enableGraphReadout(canvas, source, state, options, onPointHover) {
    const values = Array.from({ length: 21 }, (_, index) => Number(source[`mV_${index}`]));
    if (!values.some(value => Number.isFinite(value) && value !== 0)) return;

    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const padding = options.showScale
        ? { left: 52, right: 52, top: 14, bottom: 28 }
        : { left: 3, right: 3, top: 3, bottom: 3 };
    const plotWidth = options.width - padding.left - padding.right;
    const plotHeight = options.height - padding.top - padding.bottom;
    const max = Math.max(
        ...values.filter(value => Number.isFinite(value) && value !== 0),
        ...(options.averageValues || []).filter(value => Number.isFinite(value) && value !== 0),
        ...(options.faultAverageValues || []).filter(value => Number.isFinite(value) && value !== 0)
    );

    const drawHoverMarker = (index, pointerY) => {
        drawCylinderGraph(canvas, source, state, options);
        const context = canvas.getContext("2d");
        const value = values[index];
        const x = padding.left + (index / (values.length - 1)) * plotWidth;
        const hasData = Number.isFinite(value) && value !== 0;
        const y = hasData
            ? options.height - padding.bottom - (value / max) * plotHeight
            : Math.max(padding.top, Math.min(pointerY, options.height - padding.bottom));

        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        context.beginPath();
        context.moveTo(x, padding.top);
        context.lineTo(x, options.height - padding.bottom);
        context.strokeStyle = "rgba(22, 107, 77, 0.42)";
        context.lineWidth = 1;
        context.setLineDash([4, 4]);
        context.stroke();
        context.setLineDash([]);
        if (hasData) {
            context.beginPath();
            context.arc(x, y, 4.5, 0, Math.PI * 2);
            context.fillStyle = "#ffffff";
            context.fill();
            context.lineWidth = 2.5;
            context.strokeStyle = "#166b4d";
            context.stroke();
        }

        const bounds = canvas.getBoundingClientRect();
        onPointHover({
            index,
            value: hasData ? value : null,
            referenceValue: options.averageValues?.[index],
            faultReferenceValue: options.faultAverageValues?.[index],
            x: (x / options.width) * bounds.width,
            y: (y / options.height) * bounds.height
        });
    };

    canvas.classList.add("interactive-graph");
    canvas.addEventListener("pointermove", event => {
        const bounds = canvas.getBoundingClientRect();
        const graphX = (event.clientX - bounds.left) * (options.width / bounds.width);
        const graphY = (event.clientY - bounds.top) * (options.height / bounds.height);
        const estimatedIndex = Math.max(0, Math.min(
            values.length - 1,
            Math.round(((graphX - padding.left) / plotWidth) * (values.length - 1))
        ));
        drawHoverMarker(estimatedIndex, graphY);
    });
    canvas.addEventListener("pointerleave", () => {
        drawCylinderGraph(canvas, source, state, options);
        onPointHover(null);
    });
}


function renderHealthPanel(predictions, sourceRows, isValidationFile = false) {
    const panel = document.getElementById("healthPanel");
    const engineSelect = document.getElementById("engineSelect");
    const summary = document.getElementById("healthSummary");
    const metrics = document.getElementById("healthMetrics");
    const grid = document.getElementById("cylinderGrid");
    const details = document.getElementById("cylinderDetails");
    const previousCylinder = document.getElementById("previousCylinder");
    const nextCylinder = document.getElementById("nextCylinder");
    const cylinderPosition = document.getElementById("cylinderPosition");
    const sourceByCylinder = new Map(sourceRows.map(row => [`${row.engine_id}:${row.cylinder}`, row]));
    const engines = [...new Set(predictions.map(row => row.engine_id))];
    let cylinderButtons = [];
    let selectedCylinderIndex = -1;

    function updateCylinderNavigation() {
        const count = cylinderButtons.length;
        previousCylinder.disabled = selectedCylinderIndex <= 0;
        nextCylinder.disabled = count === 0 || selectedCylinderIndex >= count - 1;
        if (selectedCylinderIndex < 0) {
            cylinderPosition.textContent = count ? `${count} cylindrów · wybierz pierwszy` : "Brak cylindrów";
            nextCylinder.disabled = count === 0;
            return;
        }
        const currentButton = cylinderButtons[selectedCylinderIndex];
        cylinderPosition.textContent = `${currentButton.dataset.cylinderLabel} · ${selectedCylinderIndex + 1} z ${count}`;
    }

    function selectCylinderAt(index) {
        if (index < 0 || index >= cylinderButtons.length) return;
        cylinderButtons[index].click();
        cylinderButtons[index].scrollIntoView({ block: "nearest", inline: "nearest" });
    }

    previousCylinder.onclick = () => selectCylinderAt(selectedCylinderIndex - 1);
    nextCylinder.onclick = () => selectCylinderAt(selectedCylinderIndex < 0 ? 0 : selectedCylinderIndex + 1);

    engineSelect.replaceChildren(...engines.map(engineId => {
        const option = document.createElement("option");
        option.value = engineId;
        option.textContent = engineId;
        return option;
    }));

    function displayEngine() {
        const engineId = engineSelect.value;
        const cylinders = predictions
            .filter(row => row.engine_id === engineId)
            .sort((a, b) => Number(a.cylinder) - Number(b.cylinder));
        const nCylinders = sourceByCylinder.get(`${engineId}:${cylinders[0]?.cylinder}`)?.n_cylinders || cylinders.length;
        const faulty = cylinders.filter(row => row.label !== "ok");
        const critical = cylinders.filter(row => row.severity === "duze").length;
        const missingMeasurements = cylinders.reduce((total, row) => (
            total + countMissingMeasurements(sourceByCylinder.get(`${row.engine_id}:${row.cylinder}`))
        ), 0);
        const faultWord = faulty.length === 1
            ? "usterkę"
            : faulty.length >= 2 && faulty.length <= 4 ? "usterki" : "usterek";

        summary.textContent = faulty.length === 0
            ? `Silnik ${engineId}: wszystkie ${nCylinders} cylindrów są sprawne.`
            : `Silnik ${engineId}: wykryto ${faulty.length} ${faultWord}${critical ? `, a ${critical} wymaga pilnej uwagi` : ""}.`;
        const metricValues = [
            { value: `${cylinders.length - faulty.length}/${cylinders.length}`, label: "sprawne cylindry", state: "ok" },
            { value: faulty.length, label: "wykryte usterki", state: faulty.length ? "fault" : "ok" },
            { value: critical, label: "pilne usterki", state: critical ? "critical" : "ok" },
            { value: missingMeasurements, label: "brakujące odczyty", state: missingMeasurements ? "warning" : "ok" }
        ];
        metrics.replaceChildren(...metricValues.map(metric => {
            const item = document.createElement("div");
            const value = document.createElement("strong");
            const label = document.createElement("span");
            item.className = `health-metric ${metric.state}`;
            value.textContent = metric.value;
            label.textContent = metric.label;
            item.append(value, label);
            return item;
        }));
        grid.replaceChildren();
        details.style.display = "none";
        details.className = "cylinder-details";
        cylinderButtons = [];
        selectedCylinderIndex = -1;

        cylinders.forEach((row, index) => {
            const button = document.createElement("button");
            const state = row.label === "ok" ? "ok" : (row.label === "unknown" ? "unknown" : row.severity);
            const number = document.createElement("span");
            const label = document.createElement("span");
            const confidence = Number(row.vote_confidence ?? row.confidence);
            const statusText = cylinderStatusText(row);
            button.type = "button";
            button.className = `cylinder ${state}`;
            button.setAttribute("aria-pressed", "false");
            button.dataset.cylinderLabel = `Cylinder ${row.cylinder}`;
            button.dataset.cylinderNumber = String(row.cylinder);
            button.title = `Pokaż szczegóły: cylinder ${row.cylinder}, stan: ${statusText}`;
            button.style.animationDelay = `${index * 45}ms`;
            number.className = "cylinder-number";
            number.textContent = `Cylinder ${row.cylinder}`;
            label.className = "cylinder-label";
            label.textContent = statusText;
            button.append(number, label);
            const source = sourceByCylinder.get(`${row.engine_id}:${row.cylinder}`);
            if (source) {
                const skippedMeasurements = countMissingMeasurements(source);
                if (skippedMeasurements >= 3) {
                    const warning = document.createElement("span");
                    warning.className = "cylinder-warning";
                    warning.textContent = "!";
                    warning.title = `Uwaga: brakuje ${skippedMeasurements} odczytów. Wynik może być mniej wiarygodny.`;
                    warning.setAttribute("aria-label", warning.title);
                    button.append(warning);
                }
                const graph = document.createElement("canvas");
                graph.className = "cylinder-graph";
                graph.setAttribute("aria-hidden", "true");
                if (drawCylinderGraph(graph, source, state)) button.append(graph);
            }
            if (Number.isFinite(confidence)) {
                const confidenceLabel = document.createElement("span");
                confidenceLabel.className = "cylinder-confidence";
                confidenceLabel.textContent = `Pewność oceny: ${(confidence * 100).toFixed(0)}%`;
                button.append(confidenceLabel);
            }
            button.addEventListener("click", () => {
                if (button.classList.contains("selected") && details.style.display === "block") {
                    button.classList.remove("selected");
                    button.setAttribute("aria-pressed", "false");
                    details.style.display = "none";
                    selectedCylinderIndex = -1;
                    updateCylinderNavigation();
                    return;
                }
                grid.querySelectorAll(".cylinder").forEach(cylinder => {
                    cylinder.classList.remove("selected");
                    cylinder.setAttribute("aria-pressed", "false");
                });
                button.classList.add("selected");
                button.setAttribute("aria-pressed", "true");
                selectedCylinderIndex = index;
                updateCylinderNavigation();
                details.className = `cylinder-details ${state}`;
                const source = sourceByCylinder.get(`${row.engine_id}:${row.cylinder}`);
                const createParameterSection = (title, parameters) => {
                    const section = document.createElement("section");
                    const heading = document.createElement("h3");
                    const list = document.createElement("dl");
                    heading.textContent = title;
                    list.className = "cylinder-parameters";

                    Object.entries(parameters).forEach(([key, value]) => {
                        const item = document.createElement("div");
                        const name = document.createElement("dt");
                        const parameterValue = document.createElement("dd");
                        const confidenceFields = new Set([
                            "confidence",
                            "vote_confidence",
                            "label_vote_confidence",
                            "severity_vote_confidence",
                            "confidence_before_optimization",
                            "confidence_after_optimization",
                            "confidence_gain"
                        ]);
                        const confidence = confidenceFields.has(key)
                            ? Number(value)
                            : null;
                        const readableNames = {
                            confidence: "Pewność oceny modelu",
                            vote_confidence: "Zgodność całego werdyktu",
                            label_vote_confidence: "Zgodność typu usterki",
                            severity_vote_confidence: "Zgodność nasilenia",
                            n_model_votes: "Liczba głosujących modeli",
                            label: "Rozpoznany problem",
                            severity: "Nasilenie problemu",
                            engine_id: "Identyfikator silnika",
                            cylinder: "Numer cylindra",
                            n_cylinders: "Liczba cylindrów",
                            suspicious_frequency_range: "Podejrzane pasmo",
                            suspicious_columns: "Podejrzane pomiary",
                            imputed_columns: "Uzupełnione pasma (nie są dowodem)",
                            n_imputed_measurements: "Liczba uzupełnionych pasm",
                            confidence_optimization_applied: "Dostrojenie brakujących punktów",
                            label_before_optimization: "Werdykt przed dostrojeniem",
                            severity_before_optimization: "Nasilenie przed dostrojeniem",
                            confidence_before_optimization: "Pewność przed dostrojeniem",
                            confidence_after_optimization: "Pewność po dostrojeniu",
                            confidence_gain: "Wzrost pewności",
                            optimization_adjusted_columns: "Przesunięte brakujące punkty",
                            optimization_candidate_evaluations: "Sprawdzone warianty",
                            peak_anomaly_score: "Maksymalny wynik anomalii",
                            direction: "Kierunek odchylenia",
                            template_similarity: "Podobieństwo do wzorca usterki",
                            explanation: "Wyjaśnienie werdyktu"
                        };
                        name.textContent = readableNames[key] || key.replace("_", " ");
                        if (confidence != null && Number.isFinite(confidence)) {
                            parameterValue.textContent = `${(confidence * 100).toFixed(1)}%`;
                        } else if (typeof value === "boolean") {
                            parameterValue.textContent = value ? "Tak" : "Nie";
                        } else {
                            parameterValue.textContent = value === "" || value == null ? "—" : value;
                        }
                        item.append(name, parameterValue);
                        list.append(item);
                    });

                    section.className = "cylinder-details-section";
                    section.append(heading, list);
                    return section;
                };
                const title = document.createElement("h3");
                const detailsHeader = document.createElement("div");
                const detailsNavigation = document.createElement("nav");
                const addTodo = document.createElement("button");
                const previousDetail = document.createElement("button");
                const nextDetail = document.createElement("button");
                const prediction = { ...row };
                delete prediction.engine_id;
                delete prediction.cylinder;
                delete prediction.confidence;
                delete prediction.uncalibrated_probability_score;
                delete prediction.band_scores_json;
                delete prediction.explanation;
                delete prediction.suspicious_columns;
                delete prediction.suspicious_frequency_range;
                prediction.label = labelNames[row.label] || row.label;
                prediction.severity = severityNames[row.severity] || row.severity;
                if (prediction.label_before_optimization) {
                    prediction.label_before_optimization = labelNames[
                        prediction.label_before_optimization
                    ] || prediction.label_before_optimization;
                }
                if (prediction.severity_before_optimization) {
                    prediction.severity_before_optimization = severityNames[
                        prediction.severity_before_optimization
                    ] || prediction.severity_before_optimization;
                }

                title.className = "cylinder-details-title";
                title.textContent = `Szczegóły cylindra ${row.cylinder}`;
                detailsHeader.className = "cylinder-details-header";
                detailsNavigation.className = "cylinder-details-navigation";
                detailsNavigation.setAttribute("aria-label", "Przejdź do sąsiedniego cylindra");
                addTodo.type = "button";
                addTodo.className = "add-todo-button";
                addTodo.textContent = "+";
                addTodo.title = "Dodaj cylinder do listy to-do";
                addTodo.setAttribute("aria-label", addTodo.title);
                addTodo.onclick = () => openTodoEditorForCylinder(row, source);
                previousDetail.type = "button";
                previousDetail.textContent = "←";
                previousDetail.title = "Poprzedni cylinder";
                previousDetail.setAttribute("aria-label", previousDetail.title);
                previousDetail.disabled = index === 0;
                previousDetail.onclick = () => selectCylinderAt(index - 1);
                nextDetail.type = "button";
                nextDetail.textContent = "→";
                nextDetail.title = "Następny cylinder";
                nextDetail.setAttribute("aria-label", nextDetail.title);
                nextDetail.disabled = index === cylinders.length - 1;
                nextDetail.onclick = () => selectCylinderAt(index + 1);
                detailsNavigation.append(addTodo, previousDetail, nextDetail);
                detailsHeader.append(title, detailsNavigation);
                details.replaceChildren(detailsHeader);
                const missingMeasurements = countMissingMeasurements(source);
                const suspiciousIndices = suspiciousMeasurementIndices(row.suspicious_columns);
                const suspiciousRange = String(row.suspicious_frequency_range || "").trim();
                const hasSuspiciousBand = suspiciousIndices.length > 0
                    && suspiciousRange !== ""
                    && !suspiciousRange.toLowerCase().startsWith("brak");
                const overview = {
                    "Stan cylindra": row.label === "ok" ? "Sprawny" : "Wymaga sprawdzenia",
                    "Co wykryto": row.label === "ok"
                        ? "Nie wykryto problemu"
                        : (labelNames[row.label] || row.label),
                    "Pilność": row.label === "ok"
                        ? "Brak"
                        : (severityNames[row.severity] || row.severity),
                    "Pewność oceny": Number.isFinite(confidence)
                        ? `${(confidence * 100).toFixed(1)}%`
                        : "Brak danych",
                    "Podejrzane pasmo": hasSuspiciousBand ? suspiciousRange : "brak"
                };
                const detailsLayout = document.createElement("div");
                const detailsSummary = document.createElement("div");
                const detailsVisual = document.createElement("div");
                detailsLayout.className = "cylinder-details-layout";
                detailsSummary.className = "cylinder-details-summary";
                detailsVisual.className = "cylinder-details-visual";
                detailsSummary.append(createParameterSection("Najważniejsze informacje", overview));
                let measurementSection;
                let referenceDrawer;
                if (source) {
                    const measurementParameters = { ...source };
                    delete measurementParameters.label;
                    delete measurementParameters.severity;
                    measurementParameters["Brakujące odczyty"] = missingMeasurements;
                    const graphSection = document.createElement("section");
                    const graphTitle = document.createElement("h3");
                    const graphLegend = document.createElement("p");
                    const graphWrap = document.createElement("div");
                    const graph = document.createElement("canvas");
                    const graphTooltip = document.createElement("output");
                    const referenceSpectrum = referenceSpectra.ok || null;
                    let selectedReferenceProfile = row.label !== "ok"
                        ? findReferenceProfile(
                            row.label,
                            row.label === "unknown" ? null : row.severity
                        )
                        : null;
                    let faultReferenceSpectrum = selectedReferenceProfile?.values || null;
                    let graphOptions;
                    const highlightedIndices = hasSuspiciousBand ? suspiciousIndices : [];
                    graphSection.className = "cylinder-details-section";
                    graphTitle.textContent = "Widmo akustyczne cylindra";
                    graphLegend.className = "graph-legend";
                    if (referenceSpectrum) {
                        const healthyLegend = document.createElement("span");
                        healthyLegend.className = "graph-legend-healthy";
                        healthyLegend.textContent = `Średnie zdrowe widmo z ${referenceSpectraSource}`;
                        graphLegend.append(healthyLegend);
                    }
                    const faultLegend = document.createElement("span");
                    faultLegend.className = "graph-legend-fault";
                    const updateFaultLegend = () => {
                        faultLegend.remove();
                        if (!selectedReferenceProfile) return;
                        const severityText = selectedReferenceProfile.severity
                            ? ` · ${severityNames[selectedReferenceProfile.severity] || selectedReferenceProfile.severity}`
                            : " · bez poważności";
                        faultLegend.textContent = `Średnia „${labelNames[selectedReferenceProfile.label] || selectedReferenceProfile.label}”${severityText} · ${referenceSpectraSource}`;
                        const highlightLegend = graphLegend.querySelector(".graph-legend-highlight");
                        if (highlightLegend) highlightLegend.before(faultLegend);
                        else graphLegend.append(faultLegend);
                    };
                    updateFaultLegend();
                    if (highlightedIndices.length) {
                        const highlightLegend = document.createElement("span");
                        highlightLegend.className = "graph-legend-highlight";
                        highlightLegend.textContent = `Podejrzany fragment ${activeModelName}`;
                        graphLegend.append(highlightLegend);
                    }
                    graph.className = "cylinder-details-graph";
                    graph.setAttribute("role", "img");
                    graph.setAttribute("aria-label", `Wykres widma akustycznego cylindra ${row.cylinder}. Najedź kursorem na wykres, aby odczytać wartość punktu.`);
                    graphWrap.className = "cylinder-details-graph-wrap";
                    graphTooltip.className = "graph-tooltip";
                    graphTooltip.setAttribute("aria-live", "polite");
                    graphTooltip.textContent = "Najedź na wykres";
                    graphWrap.append(graph, graphTooltip);
                    graphSection.append(graphTitle);
                    if (graphLegend.childElementCount) graphSection.append(graphLegend);
                    graphSection.append(graphWrap);
                    measurementSection = createParameterSection("Pomiary techniczne", measurementParameters);
                    detailsVisual.append(graphSection);
                    referenceDrawer = createReferenceDrawer(selectedReferenceProfile, profile => {
                        selectedReferenceProfile = profile;
                        faultReferenceSpectrum = profile?.values || null;
                        updateFaultLegend();
                        if (!graphOptions) return;
                        graphOptions.faultAverageValues = faultReferenceSpectrum;
                        graphTooltip.classList.remove("visible");
                        drawCylinderGraph(graph, source, state, graphOptions);
                    });
                    requestAnimationFrame(() => {
                        const graphBounds = graph.getBoundingClientRect();
                        graphOptions = {
                            width: Math.round(graphBounds.width),
                            height: Math.round(graphBounds.height),
                            showScale: true,
                            averageValues: referenceSpectrum,
                            faultAverageValues: faultReferenceSpectrum,
                            highlightedIndices
                        };
                        drawCylinderGraph(graph, source, state, graphOptions);
                        enableGraphReadout(graph, source, state, graphOptions, point => {
                            if (!point) {
                                graphTooltip.classList.remove("visible");
                                return;
                            }
                            const tooltipX = Math.max(72, Math.min(point.x, graphBounds.width - 72));
                            const tooltipY = Math.max(34, point.y);
                            const referenceText = Number.isFinite(point.referenceValue) && point.referenceValue !== 0
                                ? ` · zdrowa średnia: ${point.referenceValue.toFixed(1)} mV`
                                : "";
                            const faultReferenceText = Number.isFinite(point.faultReferenceValue)
                                && point.faultReferenceValue !== 0
                                ? ` · średnia usterki: ${point.faultReferenceValue.toFixed(1)} mV`
                                : "";
                            graphTooltip.textContent = Number.isFinite(point.value)
                                ? `mV_${point.index}: ${point.value.toFixed(1)} mV${referenceText}${faultReferenceText}`
                                : `mV_${point.index}: Brak danych${referenceText}${faultReferenceText}`;
                            graphTooltip.style.left = `${tooltipX}px`;
                            graphTooltip.style.top = `${tooltipY}px`;
                            graphTooltip.classList.add("visible");
                        });
                    });
                    if (isValidationFile) {
                        detailsSummary.append(createParameterSection("Wynik podany w pliku", {
                            label: labelNames[source.label] || source.label,
                            severity: severityNames[source.severity] || source.severity
                        }));
                    }
                }
                if (referenceDrawer) detailsSummary.append(referenceDrawer);
                detailsLayout.append(detailsSummary);
                if (detailsVisual.childElementCount) detailsLayout.append(detailsVisual);
                const verdictStrip = document.createElement("div");
                const verdictCopy = document.createElement("div");
                const verdictTitle = document.createElement("strong");
                const verdictText = document.createElement("span");
                const verdictMeta = document.createElement("small");
                verdictStrip.className = "verdict-strip";
                verdictCopy.className = "verdict-copy";
                verdictTitle.textContent = "Wyjaśnienie werdyktu";
                verdictText.textContent = row.explanation || "Brak dodatkowego wyjaśnienia.";
                verdictMeta.textContent = hasSuspiciousBand
                    ? `Pasmo: ${suspiciousRange} · brakujące odczyty: ${missingMeasurements}/21`
                    : `Brak pasma ponad progiem · brakujące odczyty: ${missingMeasurements}/21`;
                verdictCopy.append(verdictTitle, verdictText);
                verdictStrip.append(verdictCopy, verdictMeta);

                const modelAssessment = document.createElement("details");
                const modelAssessmentSummary = document.createElement("summary");
                const modelAssessmentSection = createParameterSection("Parametry oceny", prediction);
                modelAssessment.className = "model-assessment";
                modelAssessmentSummary.textContent = "Ocena modelu";
                modelAssessment.append(modelAssessmentSummary, modelAssessmentSection);

                details.append(detailsLayout);
                details.append(verdictStrip, modelAssessment);
                if (measurementSection) {
                    const measurements = document.createElement("details");
                    const measurementsSummary = document.createElement("summary");
                    measurements.className = "technical-measurements";
                    measurementsSummary.textContent = "Pokaż szczegółowe odczyty techniczne";
                    measurementSection.querySelector("h3").textContent = "Odczyty mV";
                    measurements.append(measurementsSummary, measurementSection);
                    details.append(measurements);
                }
                details.style.display = "block";
                if (button.dataset.preventAutoScroll !== "true") {
                    requestAnimationFrame(() => {
                        const graph = details.querySelector(".cylinder-details-graph");
                        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
                        (graph || details).scrollIntoView({
                            behavior: reducedMotion ? "auto" : "smooth",
                            block: "center"
                        });
                    });
                }
            });
            grid.append(button);
            cylinderButtons.push(button);
        });
        updateCylinderNavigation();
    }

    diagnosticNavigator = (engineId, cylinder) => {
        const normalizedEngineId = String(engineId);
        const hasEngine = [...engineSelect.options]
            .some(option => option.value === normalizedEngineId);
        if (!hasEngine) return false;
        engineSelect.value = normalizedEngineId;
        engineSelect.dispatchEvent(new Event("change"));
        const cylinderButton = [...grid.querySelectorAll(".cylinder")]
            .find(item => item.dataset.cylinderNumber === String(cylinder));
        if (cylinderButton) cylinderButton.click();
        requestAnimationFrame(() => {
            const target = cylinderButton || document.getElementById("healthPanel");
            const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            target.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
        });
        return true;
    };

    engineSelect.onchange = displayEngine;
    panel.style.display = "none";
    displayEngine();
}

function renderFaultChart(predictions) {
    const panel = document.getElementById("faultChartPanel");
    const canvas = document.getElementById("faultChart");
    const legend = document.getElementById("faultLegend");
    const colors = ["#f59e0b", "#ef4444", "#8b5cf6", "#0ea5e9", "#ec4899"];
    const counts = predictions.reduce((total, row) => {
        if (row.label !== "ok") total[row.label] = (total[row.label] || 0) + 1;
        return total;
    }, {});
    const entries = Object.entries(counts).sort(([, first], [, second]) => second - first);

    panel.style.display = "block";
    legend.replaceChildren();

    if (entries.length === 0) {
        const message = document.createElement("p");
        message.className = "no-faults";
        message.textContent = "Nie wykryto usterek w przesłanym pliku.";
        legend.append(message);
        const context = canvas.getContext("2d");
        context.clearRect(0, 0, canvas.width, canvas.height);
        return;
    }

    const size = 240;
    const pixelRatio = window.devicePixelRatio || 1;
    canvas.width = size * pixelRatio;
    canvas.height = size * pixelRatio;
    const context = canvas.getContext("2d");
    context.scale(pixelRatio, pixelRatio);
    context.clearRect(0, 0, size, size);

    const total = entries.reduce((sum, [, count]) => sum + count, 0);
    let startAngle = -Math.PI / 2;
    entries.forEach(([label, count], index) => {
        const endAngle = startAngle + (count / total) * Math.PI * 2;
        context.beginPath();
        context.moveTo(size / 2, size / 2);
        context.arc(size / 2, size / 2, 100, startAngle, endAngle);
        context.closePath();
        context.fillStyle = colors[index % colors.length];
        context.fill();
        startAngle = endAngle;

        const item = document.createElement("div");
        const labelElement = document.createElement("span");
        const swatch = document.createElement("span");
        const countElement = document.createElement("span");
        item.className = "fault-legend-item";
        labelElement.className = "fault-legend-label";
        swatch.className = "fault-legend-swatch";
        swatch.style.background = colors[index % colors.length];
        labelElement.append(swatch, document.createTextNode(labelNames[label] || label));
        countElement.className = "fault-legend-count";
        countElement.textContent = `${count} (${Math.round((count / total) * 100)}%)`;
        item.append(labelElement, countElement);
        legend.append(item);
    });
}

function renderEngineRanking(predictions, sourceRows) {
    const panel = document.getElementById("engineRankingPanel");
    const ranking = document.getElementById("engineRanking");
    const details = document.getElementById("engineRankingDetails");
    const title = document.getElementById("engineRankingTitle");
    const sortSelect = document.getElementById("engineSort");
    const engineSelect = document.getElementById("engineSelect");
    const healthPanel = document.getElementById("healthPanel");
    const engineDetailsHome = document.getElementById("engineDetailsHome");
    const severityScore = { male: 1, srednie: 2, duze: 3, nie_dotyczy: 0 };
    const severityLabel = { 0: "brak", 1: "mała", 2: "średnia", 3: "duża" };
    const sourceByCylinder = new Map(sourceRows.map(row => [`${row.engine_id}:${row.cylinder}`, row]));
    const engines = [...new Set(predictions.map(row => row.engine_id))].map(engineId => {
        const cylinders = predictions.filter(row => row.engine_id === engineId);
        const faults = cylinders.filter(row => row.label !== "ok");
        const severityValues = faults.map(row => severityScore[row.severity] || 1);
        const faultRisk = severityValues.reduce((sum, value) => sum + value, 0);
        const maxSeverity = severityValues.length ? Math.max(...severityValues) : 0;
        const criticalCount = faults.filter(row => row.severity === "duze").length;
        const confidences = cylinders
            .map(row => Number(row.vote_confidence ?? row.confidence))
            .filter(Number.isFinite);
        const minimumConfidence = confidences.length
            ? Math.min(...confidences)
            : 0;
        const skippedMeasurements = cylinders.reduce((sum, cylinder) => {
            const source = sourceByCylinder.get(`${cylinder.engine_id}:${cylinder.cylinder}`);
            if (!source) return sum;
            return sum + Array.from({ length: 21 }, (_, index) => Number(source[`mV_${index}`]))
                .filter(value => !Number.isFinite(value) || value === 0).length;
        }, 0);
        const riskScore = faultRisk + skippedMeasurements / 21;
        const faultPenalty = cylinders.length ? faultRisk / (cylinders.length * 3) : 1;
        const dataPenalty = cylinders.length
            ? skippedMeasurements / (cylinders.length * 21)
            : 1;
        return {
            engineId,
            faultCount: faults.length,
            criticalCount,
            maxSeverity,
            skippedMeasurements,
            riskScore,
            uncertainty: 1 - minimumConfidence,
            healthScore: Math.max(0, 100 * (1 - 0.8 * faultPenalty - 0.2 * dataPenalty))
        };
    });
    let selectedEngineId = null;

    function placeHealthPanel(engineId) {
        const selectedCard = [...ranking.querySelectorAll(".engine-rating")]
            .find(card => card.dataset.engineId === engineId);
        if (!selectedCard) return;
        ranking.querySelectorAll(".engine-rating").forEach(card => {
            card.classList.toggle("active", card === selectedCard);
        });
        selectedCard.after(healthPanel);
        healthPanel.style.display = "block";
    }

    const sortModes = {
        healthiest: {
            title: "Najzdrowsze silniki",
            compare: (first, second) =>
                second.healthScore - first.healthScore
                || first.riskScore - second.riskScore
                || first.uncertainty - second.uncertainty,
            metric: engine => `Zdrowie: ${engine.healthScore.toFixed(0)}%`
        },
        severity: {
            title: "Silniki z najpoważniejszą usterką",
            compare: (first, second) =>
                second.maxSeverity - first.maxSeverity
                || second.criticalCount - first.criticalCount
                || second.riskScore - first.riskScore,
            metric: engine => `Poważność: ${severityLabel[engine.maxSeverity]}`
        },
        risk: {
            title: "Silniki o największym ryzyku",
            compare: (first, second) =>
                second.riskScore - first.riskScore
                || second.maxSeverity - first.maxSeverity
                || second.uncertainty - first.uncertainty,
            metric: engine => `Ryzyko: ${engine.riskScore.toFixed(1)}`
        },
        uncertainty: {
            title: "Silniki o największej niepewności",
            compare: (first, second) =>
                second.uncertainty - first.uncertainty
                || second.riskScore - first.riskScore,
            metric: engine => `Niepewność: ${(engine.uncertainty * 100).toFixed(1)}%`
        }
    };

    function renderRanking() {
        if (!engineDetailsHome.contains(healthPanel)) engineDetailsHome.append(healthPanel);
        healthPanel.style.display = "none";
        const modeName = sortModes[sortSelect.value] ? sortSelect.value : "risk";
        const mode = sortModes[modeName];
        const sortedEngines = [...engines]
            .sort((first, second) => mode.compare(first, second) || first.engineId.localeCompare(second.engineId));
        title.textContent = `${mode.title} (${sortedEngines.length})`;
        ranking.replaceChildren();
        details.replaceChildren();
        details.style.display = "none";

        sortedEngines.forEach((engine, index) => {
            const card = document.createElement("button");
            const rank = document.createElement("span");
            const content = document.createElement("div");
            const heading = document.createElement("strong");
            const description = document.createElement("span");
            const metric = document.createElement("span");
            const faultWord = engine.faultCount === 1
                ? "usterka"
                : engine.faultCount >= 2 && engine.faultCount <= 4 ? "usterki" : "usterek";
            card.type = "button";
            card.className = `engine-rating engine-rating-${modeName}`;
            card.dataset.engineId = engine.engineId;
            card.title = `Przejdź do silnika ${engine.engineId}`;
            rank.className = "engine-rank";
            rank.textContent = index + 1;
            heading.textContent = engine.engineId;
            description.textContent = `${engine.faultCount} ${faultWord} · ${engine.criticalCount} pilne`;
            metric.className = "engine-risk";
            metric.textContent = mode.metric(engine);
            content.append(heading, description);
            card.append(rank, content, metric);
            card.addEventListener("click", () => {
                selectedEngineId = engine.engineId;
                engineSelect.value = engine.engineId;
                engineSelect.dispatchEvent(new Event("change"));
                requestAnimationFrame(() => {
                    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
                    healthPanel.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "nearest" });
                    engineSelect.focus({ preventScroll: true });
                });
            });
            ranking.append(card);
        });
        if (selectedEngineId) placeHealthPanel(selectedEngineId);
    }

    const displaySelectedEngine = engineSelect.onchange;
    engineSelect.onchange = event => {
        displaySelectedEngine?.call(engineSelect, event);
        selectedEngineId = engineSelect.value;
        placeHealthPanel(selectedEngineId);
    };
    sortSelect.onchange = () => {
        selectedEngineId = null;
        if (!engineDetailsHome.contains(healthPanel)) engineDetailsHome.append(healthPanel);
        healthPanel.style.display = "none";
        renderRanking();
    };
    panel.style.display = "block";
    renderRanking();
}

previewButton.addEventListener("click", async function () {
    const input = document.getElementById("file");
    const status = document.getElementById("status");
    const preview = document.getElementById("preview");
    const previewPanel = document.getElementById("previewPanel");
    const file = input.files[0];

    if (previewPanel.style.display === "block") {
        previewPanel.style.display = "none";
        return;
    }

    if (!file) {
        status.textContent = "Wybierz plik, aby zobaczyć podgląd.";
        previewPanel.style.display = "none";
        return;
    }

    if (file.size > MAX_FILE_SIZE) {
        status.textContent = "Plik musi być mniejszy niż 10 MB.";
        previewPanel.style.display = "none";
        return;
    }

    try {
        const fullText = await readSourceCsv(file);
        const text = fullText.slice(0, 1000);
        preview.textContent = `${text}${fullText.length > 1000 ? "\n..." : ""}` || "Plik jest pusty.";
        previewPanel.style.display = "block";
        status.textContent = `Podgląd pliku: ${file.name}`;
    } catch (error) {
        console.error("PREVIEW ERROR:", error);
        status.textContent = "Nie udało się odczytać pliku.";
        previewPanel.style.display = "none";
    }
});

closePreviewButton.addEventListener("click", function () {
    document.getElementById("previewPanel").style.display = "none";
});

button.addEventListener("click", async function () {
    const input = document.getElementById("file");
    const status = document.getElementById("status");
    const download = document.getElementById("download");
    const healthPanel = document.getElementById("healthPanel");
    const engineRankingPanel = document.getElementById("engineRankingPanel");
    const faultChartPanel = document.getElementById("faultChartPanel");
    const selectedModel = document.getElementById("modelSelect").value || "model2";

    const file = input.files[0];

    if (!file) {
        status.textContent = "Wybierz plik.";
        download.style.display = "none";
        healthPanel.style.display = "none";
        engineRankingPanel.style.display = "none";
        faultChartPanel.style.display = "none";
        return;
    }

    if (!file.name.toLowerCase().endsWith(".csv") && !file.name.toLowerCase().endsWith(".zip")) {
        status.textContent = "Wybierz plik CSV lub ZIP zawierający plik CSV.";
        return;
    }

    if (file.size > MAX_FILE_SIZE) {
        status.textContent = "Plik musi być mniejszy niż 10 MB.";
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    status.textContent = "Przetwarzanie...";
    diagnosticNavigator = null;
    diagnosticPredictions = [];
    diagnosticSourceRows = [];
    download.style.display = "none";
    healthPanel.style.display = "none";
    engineRankingPanel.style.display = "none";
    faultChartPanel.style.display = "none";

    try {
        const response = await fetch(apiUrl(`/api/predict?model=${encodeURIComponent(selectedModel)}`), {
            method: "POST",
            body: formData
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok) {
            throw new Error(payload?.detail || `Serwer zwrócił błąd HTTP ${response.status}.`);
        }
        if (!payload || !Array.isArray(payload.results)) {
            throw new Error("Serwer zwrócił nieprawidłowy format odpowiedzi modelu.");
        }

        referenceSpectra = payload.reference_spectra || {};
        activeModelName = payload.selected_model || selectedModel;
        referenceProfiles = Array.isArray(payload.reference_profiles)
            ? payload.reference_profiles
            : [];
        referenceSpectraSource = payload.reference_spectra_source || "valid.csv";
        const predictions = payload.results;
        const sourceText = await readSourceCsv(file);
        const sourceRows = parseCsv(sourceText);
        diagnosticPredictions = predictions;
        diagnosticSourceRows = sourceRows;
        const isValidationFile = sourceRows.some(row =>
            Object.prototype.hasOwnProperty.call(row, "label")
            && Object.prototype.hasOwnProperty.call(row, "severity")
        );

        const exportRows = predictions.map(row => ({
            engine_id: row.engine_id,
            cylinder: row.cylinder,
            label: row.label,
            severity: row.severity
        }));
        const text = rowsToCsv(exportRows);
        const resultBlob = new Blob([text], { type: "text/csv;charset=utf-8" });
        if (resultUrl) URL.revokeObjectURL(resultUrl);
        const url = URL.createObjectURL(resultBlob);
        resultUrl = url;

        download.href = url;
        download.download = `wynik_${payload.selected_model || selectedModel}.csv`;
        download.textContent = isValidationFile
            ? "Pobierz wynik CSV"
            : "Pobierz predykcję CSV";
        download.title = "CSV: engine_id, cylinder, label, severity";
        download.setAttribute(
            "aria-label",
            `${download.textContent}: engine_id, cylinder, label, severity`
        );
        download.style.display = "inline-flex";

        renderHealthPanel(predictions, sourceRows, isValidationFile);
        renderEngineRanking(predictions, sourceRows);
        renderFaultChart(predictions);
        persistEngineSnapshotsForExistingTodos().catch(error => {
            console.warn("Nie udało się uzupełnić starszych snapshotów silników:", error);
        });

        const voteInfo = payload.model_votes
            ? ` Głosowało ${payload.model_votes} modeli.`
            : "";
        const modelLabel = payload.selected_model || selectedModel;
        status.textContent = isValidationFile
            ? `Analiza ${modelLabel} zakończona.${voteInfo}`
            : `Predykcja ${modelLabel} zakończona.${voteInfo}`;
    } catch (error) {
        console.error("ERROR:", error);
        status.textContent = "Błąd: " + error.message;
    }
});

function storedAuthToken() {
    try {
        return localStorage.getItem("piher2-auth-token");
    } catch {
        return null;
    }
}

function saveAuthToken(token) {
    authToken = token || null;
    try {
        if (authToken) localStorage.setItem("piher2-auth-token", authToken);
        else localStorage.removeItem("piher2-auth-token");
    } catch {
        // Sesja nadal działa w bieżącej karcie.
    }
}

async function apiRequest(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
    let body = options.body;
    if (body && !(body instanceof FormData) && typeof body !== "string") {
        headers.set("Content-Type", "application/json");
        body = JSON.stringify(body);
    }
    const response = await fetch(apiUrl(path), { ...options, headers, body });
    const payload = response.status === 204
        ? null
        : await response.json().catch(() => null);
    if (!response.ok) {
        if (response.status === 401 && !path.startsWith("/api/auth/login")) {
            clearSession(false);
        }
        throw new Error(payload?.detail || `Serwer zwrócił błąd HTTP ${response.status}.`);
    }
    return payload;
}

function openDialog(dialog) {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
}

function closeDialog(dialog) {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
}

function setFormMessage(element, text = "", kind = "") {
    element.textContent = text;
    element.className = `form-message${kind ? ` ${kind}` : ""}`;
}

function clearSession(refresh = true) {
    saveAuthToken(null);
    currentUser = null;
    employees = [];
    todoItems = [];
    updateAccountInterface();
    if (refresh) renderTodos();
}

function updateAccountInterface() {
    const loggedIn = Boolean(currentUser);
    const manager = loggedIn && currentUser.role === "manager";
    document.getElementById("guestActions").hidden = loggedIn;
    document.getElementById("userActions").hidden = !loggedIn;
    document.getElementById("todoGuestPanel").hidden = loggedIn;
    document.getElementById("todoWorkspace").hidden = !loggedIn;
    document.getElementById("addEmployeeButton").hidden = !manager;
    document.getElementById("employeeDirectory").hidden = !manager;
    document.getElementById("currentUserLabel").textContent = loggedIn
        ? `${currentUser.display_name} · ${currentUser.role === "manager" ? "przełożony" : "pracownik"}`
        : "";
    document.getElementById("workspaceTabLabel").textContent = manager ? "Pracownicy" : "To-do";
    document.getElementById("todoTitle").textContent = manager ? "Pracownicy" : "Lista to-do cylindrów";
    document.getElementById("aboutWorkspaceButton").textContent = manager ? "Otwórz pracowników" : "Otwórz zadania";
    document.getElementById("todoSubtitle").textContent = loggedIn
        ? (currentUser.role === "manager"
            ? "Zadania Twoje i pracowników, wraz z historią wykonania."
            : "Twoje zadania serwisowe i zakończone naprawy.")
        : "Zaloguj się, aby zapisywać zadania serwisowe.";
    updateEmployeeControls();
    renderEmployeeDirectory();
    updateTodoBadge();
}

function setAuthMode(mode) {
    const registering = mode === "register";
    document.getElementById("authForm").dataset.mode = registering ? "register" : "login";
    document.getElementById("authDialogTitle").textContent = registering ? "Załóż konto przełożonego" : "Logowanie";
    document.getElementById("authDisplayNameField").hidden = !registering;
    document.getElementById("authDisplayName").required = registering;
    document.getElementById("authPassword").autocomplete = registering ? "new-password" : "current-password";
    document.getElementById("authSubmit").textContent = registering ? "Załóż konto" : "Zaloguj się";
    document.getElementById("authLoginMode").classList.toggle("active", !registering);
    document.getElementById("authRegisterMode").classList.toggle("active", registering);
    setFormMessage(document.getElementById("authMessage"));
}

function showAuth(mode = "login") {
    setAuthMode(mode);
    openDialog(document.getElementById("authDialog"));
    requestAnimationFrame(() => document.getElementById("authUsername").focus());
}

async function loadEmployees() {
    if (!currentUser || currentUser.role !== "manager") {
        employees = [];
        updateEmployeeControls();
        renderEmployeeDirectory();
        return;
    }
    const payload = await apiRequest("/api/employees");
    employees = payload.employees || [];
    updateEmployeeControls();
    renderEmployeeDirectory();
}

function openEmployeePasswordDialog(employee) {
    const form = document.getElementById("employeePasswordForm");
    form.reset();
    document.getElementById("passwordEmployeeId").value = employee.id;
    document.getElementById("passwordEmployeeName").textContent =
        `${employee.display_name} · login: ${employee.username}`;
    setFormMessage(document.getElementById("employeePasswordMessage"));
    openDialog(document.getElementById("employeePasswordDialog"));
    requestAnimationFrame(() => document.getElementById("newEmployeePassword").focus());
}

function renderEmployeeDirectory() {
    const directory = document.getElementById("employeeDirectory");
    const list = document.getElementById("employeeList");
    const empty = document.getElementById("employeeListEmpty");
    const manager = currentUser?.role === "manager";
    directory.hidden = !manager;
    if (!manager) {
        list.replaceChildren();
        empty.hidden = true;
        return;
    }
    const selectedOwner = document.getElementById("employeeFilter").value || "all";
    const people = [
        { ...currentUser, isSelf: true },
        ...employees.map(employee => ({ ...employee, isSelf: false }))
    ].filter(person => selectedOwner === "all" || String(person.id) === selectedOwner);
    const visibleTodos = filteredTodos();
    const cards = people.map(employee => {
        const card = document.createElement("article");
        const header = document.createElement("div");
        const identity = document.createElement("div");
        const name = document.createElement("strong");
        const login = document.createElement("span");
        const counts = document.createElement("span");
        const passwordButton = document.createElement("button");
        const tasks = document.createElement("div");
        const emptyTasks = document.createElement("p");
        const employeeTodos = todoItems.filter(item => item.owner_id === employee.id);
        const displayedTodos = visibleTodos.filter(item => item.owner_id === employee.id);
        card.className = "employee-card";
        header.className = "employee-card-header";
        identity.className = "employee-identity";
        name.textContent = employee.isSelf
            ? `Twoje zadania · ${employee.display_name}`
            : (employee.display_name || employee.username);
        login.textContent = `Login: ${employee.username}`;
        const doneCount = employeeTodos.filter(item => item.status === "done").length;
        const todoCount = employeeTodos.length;
        counts.className = "employee-task-count";
        counts.textContent = `${doneCount}/${todoCount} zakończonych`;
        identity.append(name, login);
        header.append(identity, counts);
        if (!employee.isSelf) {
            passwordButton.type = "button";
            passwordButton.className = "secondary-button employee-password-button";
            passwordButton.textContent = "Zmień hasło";
            passwordButton.onclick = () => openEmployeePasswordDialog(employee);
            header.append(passwordButton);
        }
        tasks.className = "employee-card-todos";
        tasks.replaceChildren(...displayedTodos.map(createTodoCard));
        emptyTasks.className = "employee-todos-empty";
        emptyTasks.textContent = todoFilter === "all"
            ? "Brak zadań dla tej osoby."
            : `Brak zadań w filtrze „${todoStatusName(todoFilter)}”.`;
        emptyTasks.hidden = displayedTodos.length > 0;
        card.append(header, tasks, emptyTasks);
        return card;
    });
    list.replaceChildren(...cards);
    empty.hidden = employees.length > 0;
}

function accountOptions(includeAll = false) {
    const options = [];
    if (includeAll) options.push({ id: "all", label: "Wszyscy pracownicy i ja" });
    if (currentUser) options.push({ id: String(currentUser.id), label: `${currentUser.display_name} (ja)` });
    employees.forEach(employee => options.push({
        id: String(employee.id),
        label: employee.display_name || employee.username
    }));
    return options;
}

function fillSelect(select, options, selectedValue = null) {
    const previous = selectedValue == null ? select.value : String(selectedValue);
    select.replaceChildren(...options.map(item => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.label;
        return option;
    }));
    if (options.some(item => item.id === previous)) select.value = previous;
}

function updateEmployeeControls() {
    const manager = currentUser?.role === "manager";
    const filterLabel = document.getElementById("employeeFilterLabel");
    const ownerField = document.getElementById("todoOwnerField");
    filterLabel.hidden = !manager;
    ownerField.hidden = !manager;
    if (manager) {
        fillSelect(document.getElementById("employeeFilter"), accountOptions(true));
        fillSelect(document.getElementById("todoOwner"), accountOptions(false));
    }
}

async function loadTodos() {
    if (!currentUser) {
        todoItems = [];
        renderTodos();
        return;
    }
    const payload = await apiRequest("/api/todos");
    todoItems = payload.todos || [];
    renderTodos();
}

async function loadWorkspaceData() {
    if (!currentUser) return;
    await loadEmployees();
    await loadTodos();
}

function updateTodoBadge() {
    const badge = document.getElementById("todoBadge");
    const count = todoItems.filter(item => item.status !== "done").length;
    badge.hidden = !currentUser || count === 0;
    badge.textContent = count;
}

function todoStatusName(status) {
    return { todo: "Do zrobienia", in_progress: "W trakcie", done: "Skończone" }[status] || status;
}

function drawTodoThumbnail(canvas, values, severity) {
    const context = canvas.getContext("2d");
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(76, canvas.clientWidth || 76);
    const height = 46;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const numeric = (values || []).map(value => Number(value));
    const finite = numeric.filter(Number.isFinite);
    if (finite.length < 2) return;
    const minimum = Math.min(...finite);
    const maximum = Math.max(...finite);
    const span = Math.max(maximum - minimum, 1e-9);
    const color = { male: "#a16207", srednie: "#d97706", duze: "#dc2626" }[severity] || "#6d28d9";
    context.beginPath();
    let started = false;
    numeric.forEach((value, index) => {
        if (!Number.isFinite(value)) {
            started = false;
            return;
        }
        const x = 2 + (index / Math.max(numeric.length - 1, 1)) * (width - 4);
        const y = height - 3 - ((value - minimum) / span) * (height - 7);
        if (!started) context.moveTo(x, y);
        else context.lineTo(x, y);
        started = true;
    });
    context.lineWidth = 2;
    context.strokeStyle = color;
    context.stroke();
}

function filteredTodos() {
    const owner = document.getElementById("employeeFilter")?.value || "all";
    return todoItems.filter(item => {
        const statusMatches = todoFilter === "all" || item.status === todoFilter;
        const ownerMatches = currentUser?.role !== "manager" || owner === "all" || String(item.owner_id) === owner;
        return statusMatches && ownerMatches;
    });
}

function updateActiveTodoFilter() {
    const output = document.getElementById("activeTodoFilter");
    const statusLabel = todoFilter === "all" ? "Wszystkie" : todoStatusName(todoFilter);
    let ownerLabel = "";
    if (currentUser?.role === "manager") {
        const selectedOption = document.getElementById("employeeFilter").selectedOptions[0];
        ownerLabel = selectedOption ? ` · osoba: ${selectedOption.textContent}` : "";
    }
    output.textContent = `Wybrany filtr: ${statusLabel}${ownerLabel}`;
}

function renderEmployeeSummary(items) {
    const summary = document.getElementById("employeeSummary");
    if (currentUser?.role !== "manager") {
        summary.hidden = true;
        return;
    }
    const owner = document.getElementById("employeeFilter").value;
    const selected = accountOptions(false).find(option => option.id === owner);
    const allForOwner = owner === "all" ? todoItems : todoItems.filter(item => String(item.owner_id) === owner);
    const finished = allForOwner.filter(item => item.status === "done").length;
    summary.hidden = false;
    summary.textContent = `${selected?.label || "Cały zespół"}: ${allForOwner.length} zadań, ${finished} zakończonych, ${allForOwner.length - finished} aktywnych.`;
}

function createTodoCard(item) {
    const card = document.createElement("article");
    const thumbnail = document.createElement("div");
    const thumbnailTitle = document.createElement("strong");
    const canvas = document.createElement("canvas");
    const content = document.createElement("div");
    const owner = document.createElement("p");
    const title = document.createElement("h3");
    const meta = document.createElement("p");
    const note = document.createElement("p");
    const actions = document.createElement("div");
    const status = document.createElement("select");
    const edit = document.createElement("button");
    const remove = document.createElement("button");

    card.className = "todo-card";
    card.dataset.status = item.status;
    card.tabIndex = 0;
    card.title = `Otwórz silnik ${item.engine_id}, cylinder ${item.cylinder}`;
    card.setAttribute("aria-label", card.title);
    thumbnail.className = `todo-thumbnail ${item.severity}`;
    thumbnailTitle.textContent = `Cylinder ${item.cylinder}`;
    canvas.setAttribute("aria-label", `Miniatura widma cylindra ${item.cylinder}`);
    thumbnail.append(thumbnailTitle, canvas);
    content.className = "todo-content";
    owner.className = "todo-owner";
    owner.textContent = currentUser?.role === "manager"
        ? `Pracownik: ${item.owner_display_name}`
        : `Zadanie: ${item.owner_display_name}`;
    title.textContent = item.fault_label;
    meta.className = "todo-meta";
    meta.textContent = `Silnik ${item.engine_id} · cylinder ${item.cylinder} · ${severityNames[item.severity] || "bez powagi"}`;
    note.className = "todo-note";
    note.textContent = item.note || (item.completed_at
        ? `Zakończono: ${new Date(item.completed_at).toLocaleString("pl-PL")}`
        : "Brak dodatkowej notatki.");
    actions.className = "todo-card-actions";
    ["todo", "in_progress", "done"].forEach(value => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = todoStatusName(value);
        status.append(option);
    });
    status.value = item.status;
    status.title = "Zmień stan zadania";
    status.addEventListener("change", async () => {
        try {
            await apiRequest(`/api/todos/${item.id}`, { method: "PATCH", body: { status: status.value } });
            await loadTodos();
        } catch (error) {
            status.value = item.status;
            window.alert(error.message);
        }
    });
    edit.type = "button";
    edit.textContent = "Edytuj";
    edit.onclick = () => openTodoEditorForItem(item);
    remove.type = "button";
    remove.className = "danger";
    remove.textContent = "Usuń";
    remove.onclick = async () => {
        if (!window.confirm(`Usunąć zadanie dla ${item.engine_id}, cylinder ${item.cylinder}?`)) return;
        try {
            await apiRequest(`/api/todos/${item.id}`, { method: "DELETE" });
            await loadTodos();
        } catch (error) {
            window.alert(error.message);
        }
    };
    actions.append(status, edit, remove);
    content.append(owner, title, meta, note, actions);
    card.append(thumbnail, content);
    const navigate = event => {
        if (event.target.closest("button, select, input, textarea, a")) return;
        if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        navigateToTodoCylinder(item);
    };
    card.addEventListener("click", navigate);
    card.addEventListener("keydown", navigate);
    requestAnimationFrame(() => drawTodoThumbnail(canvas, item.spectrum, item.severity));
    return card;
}

function renderTodos() {
    const board = document.getElementById("todoBoard");
    const empty = document.getElementById("todoEmpty");
    if (!currentUser) {
        board.replaceChildren();
        empty.hidden = true;
        updateTodoBadge();
        return;
    }
    updateActiveTodoFilter();
    if (currentUser.role === "manager") {
        board.replaceChildren();
        empty.hidden = true;
        document.getElementById("employeeSummary").hidden = true;
        renderEmployeeDirectory();
        updateTodoBadge();
        return;
    }
    const items = filteredTodos();
    board.replaceChildren(...items.map(createTodoCard));
    empty.hidden = items.length > 0;
    renderEmployeeSummary(items);
    updateTodoBadge();
}

function cylinderSpectrum(source) {
    return Array.from({ length: 21 }, (_, index) => {
        const value = Number(source?.[`mV_${index}`]);
        return Number.isFinite(value) ? value : null;
    });
}

function engineSnapshot(engineId) {
    const normalized = String(engineId);
    const predictions = diagnosticPredictions.filter(row => String(row.engine_id) === normalized);
    const sourceRows = diagnosticSourceRows.filter(row => String(row.engine_id) === normalized);
    if (!predictions.length || !sourceRows.length) return null;
    return {
        model: activeModelName,
        predictions,
        source_rows: sourceRows
    };
}

function loadEngineSnapshot(snapshot) {
    if (!snapshot || !Array.isArray(snapshot.predictions) || !Array.isArray(snapshot.source_rows)) {
        return false;
    }
    if (!snapshot.predictions.length || !snapshot.source_rows.length) return false;
    diagnosticPredictions = snapshot.predictions;
    diagnosticSourceRows = snapshot.source_rows;
    activeModelName = snapshot.model || activeModelName;
    const modelSelect = document.getElementById("modelSelect");
    if ([...modelSelect.options].some(option => option.value === activeModelName)) {
        modelSelect.value = activeModelName;
    }
    const validation = diagnosticSourceRows.some(row =>
        Object.prototype.hasOwnProperty.call(row, "label")
        && Object.prototype.hasOwnProperty.call(row, "severity")
    );
    renderHealthPanel(diagnosticPredictions, diagnosticSourceRows, validation);
    renderEngineRanking(diagnosticPredictions, diagnosticSourceRows);
    renderFaultChart(diagnosticPredictions);
    return true;
}

async function persistEngineSnapshotsForExistingTodos() {
    if (!currentUser || !todoItems.length) return;
    const pending = todoItems.filter(item => !item.engine_snapshot && engineSnapshot(item.engine_id));
    if (!pending.length) return;
    await Promise.all(pending.map(item => apiRequest(`/api/todos/${item.id}`, {
        method: "PATCH",
        body: { engine_snapshot: engineSnapshot(item.engine_id) }
    })));
    await loadTodos();
}

function populateTodoEditor(context) {
    const editing = Boolean(context.id);
    document.getElementById("todoEditorId").value = context.id || "";
    document.getElementById("todoEditorTitle").textContent = editing ? "Edytuj zadanie" : "Dodaj do to-do";
    document.getElementById("todoEditorEngine").textContent = `Silnik ${context.engine_id}`;
    document.getElementById("todoEditorCylinder").textContent = `Cylinder ${context.cylinder}`;
    document.getElementById("todoFault").value = context.fault_label || "kontrola cylindra";
    document.getElementById("todoSeverity").value = context.severity || "nie_dotyczy";
    document.getElementById("todoStatus").value = context.status || "todo";
    document.getElementById("todoNote").value = context.note || "";
    if (currentUser?.role === "manager") {
        fillSelect(document.getElementById("todoOwner"), accountOptions(false), context.owner_id || currentUser.id);
    }
    setFormMessage(document.getElementById("todoEditorMessage"));
}

function showTodoEditor(context) {
    todoEditorContext = context;
    populateTodoEditor(context);
    openDialog(document.getElementById("todoEditorDialog"));
}

function openTodoEditorForCylinder(row, source) {
    const context = {
        engine_id: row.engine_id,
        cylinder: Number(row.cylinder),
        n_cylinders: Number(source?.n_cylinders) || null,
        fault_label: row.label === "ok" ? "kontrola cylindra" : (labelNames[row.label] || row.label),
        severity: row.label === "ok" ? "nie_dotyczy" : row.severity,
        status: "todo",
        note: "",
        spectrum: cylinderSpectrum(source),
        engine_snapshot: engineSnapshot(row.engine_id),
        owner_id: currentUser?.id || null
    };
    todoEditorContext = context;
    if (!currentUser) {
        showAuth("login");
        setFormMessage(document.getElementById("authMessage"), "Zaloguj się, aby zapisać ten cylinder.");
        return;
    }
    showTodoEditor(context);
}

function openTodoEditorForItem(item) {
    showTodoEditor({ ...item });
}

function navigateToTodoCylinder(item) {
    switchAppView("diagnostics");
    let found = diagnosticNavigator?.(item.engine_id, item.cylinder) || false;
    if (!found && loadEngineSnapshot(item.engine_snapshot)) {
        found = diagnosticNavigator?.(item.engine_id, item.cylinder) || false;
    }
    const status = document.getElementById("status");
    if (found) {
        status.textContent = `Otwarto silnik ${item.engine_id}, cylinder ${item.cylinder}.`;
        return;
    }
    status.textContent = `Wczytaj plik zawierający silnik ${item.engine_id}, aby otworzyć cylinder ${item.cylinder}.`;
    requestAnimationFrame(() => document.querySelector(".upload-box").scrollIntoView({ behavior: "smooth", block: "center" }));
}

function switchAppView(name) {
    const diagnostics = name === "diagnostics";
    const todo = name === "todo";
    const about = name === "about";
    document.getElementById("diagnosticsView").hidden = !diagnostics;
    document.getElementById("todoView").hidden = !todo;
    document.getElementById("aboutView").hidden = !about;
    document.getElementById("diagnosticsTab").classList.toggle("active", diagnostics);
    document.getElementById("todoTab").classList.toggle("active", todo);
    document.getElementById("diagnosticsTab").setAttribute("aria-selected", String(diagnostics));
    document.getElementById("todoTab").setAttribute("aria-selected", String(todo));
    if (todo && currentUser) loadWorkspaceData().catch(error => window.alert(error.message));
    window.scrollTo({ top: 0, behavior: "smooth" });
}

async function restoreSession() {
    saveAuthToken(storedAuthToken());
    if (!authToken) {
        updateAccountInterface();
        return;
    }
    try {
        const payload = await apiRequest("/api/auth/me");
        currentUser = payload.user;
        updateAccountInterface();
        await loadWorkspaceData();
    } catch {
        clearSession();
    }
}

function showEmployeeCreationDialog() {
    document.getElementById("employeeForm").reset();
    setFormMessage(document.getElementById("employeeMessage"));
    openDialog(document.getElementById("employeeDialog"));
}

function initializeAccountsAndTodos() {
    document.getElementById("diagnosticsTab").onclick = () => switchAppView("diagnostics");
    document.getElementById("todoTab").onclick = () => switchAppView("todo");
    document.getElementById("aboutLink").onclick = event => {
        event.preventDefault();
        switchAppView("about");
    };
    document.getElementById("aboutDiagnosticsButton").onclick = () => switchAppView("diagnostics");
    document.getElementById("aboutWorkspaceButton").onclick = () => switchAppView("todo");
    document.getElementById("loginButton").onclick = () => showAuth("login");
    document.getElementById("registerButton").onclick = () => showAuth("register");
    document.getElementById("todoLoginButton").onclick = () => showAuth("login");
    document.getElementById("authLoginMode").onclick = () => setAuthMode("login");
    document.getElementById("authRegisterMode").onclick = () => setAuthMode("register");
    document.querySelectorAll("[data-close-dialog]").forEach(control => {
        control.addEventListener("click", () => closeDialog(document.getElementById(control.dataset.closeDialog)));
    });
    document.getElementById("logoutButton").onclick = async () => {
        try { await apiRequest("/api/auth/logout", { method: "POST" }); } catch { /* sesja i tak jest usuwana */ }
        clearSession();
        switchAppView("diagnostics");
    };
    document.getElementById("authForm").addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget;
        const mode = form.dataset.mode || "login";
        const message = document.getElementById("authMessage");
        setFormMessage(message, "Zapisywanie...");
        try {
            const body = {
                username: document.getElementById("authUsername").value,
                password: document.getElementById("authPassword").value
            };
            if (mode === "register") body.display_name = document.getElementById("authDisplayName").value;
            const payload = await apiRequest(`/api/auth/${mode}`, { method: "POST", body });
            saveAuthToken(payload.token);
            currentUser = payload.user;
            updateAccountInterface();
            await loadWorkspaceData();
            closeDialog(document.getElementById("authDialog"));
            form.reset();
            if (todoEditorContext && !todoEditorContext.id) showTodoEditor({ ...todoEditorContext, owner_id: currentUser.id });
        } catch (error) {
            setFormMessage(message, error.message, "error");
        }
    });
    document.getElementById("addEmployeeButton").onclick = showEmployeeCreationDialog;
    document.getElementById("directoryAddEmployeeButton").onclick = showEmployeeCreationDialog;
    document.getElementById("employeeForm").addEventListener("submit", async event => {
        event.preventDefault();
        const message = document.getElementById("employeeMessage");
        try {
            await apiRequest("/api/employees", {
                method: "POST",
                body: {
                    display_name: document.getElementById("employeeDisplayName").value,
                    username: document.getElementById("employeeUsername").value,
                    password: document.getElementById("employeePassword").value
                }
            });
            setFormMessage(message, "Konto pracownika zostało utworzone.", "success");
            await loadWorkspaceData();
            setTimeout(() => closeDialog(document.getElementById("employeeDialog")), 700);
        } catch (error) {
            setFormMessage(message, error.message, "error");
        }
    });
    document.getElementById("employeePasswordForm").addEventListener("submit", async event => {
        event.preventDefault();
        const message = document.getElementById("employeePasswordMessage");
        const password = document.getElementById("newEmployeePassword").value;
        const confirmation = document.getElementById("confirmEmployeePassword").value;
        if (password !== confirmation) {
            setFormMessage(message, "Hasła nie są takie same.", "error");
            return;
        }
        const employeeId = Number(document.getElementById("passwordEmployeeId").value);
        setFormMessage(message, "Zapisywanie...");
        try {
            await apiRequest(`/api/employees/${employeeId}/password`, {
                method: "PATCH",
                body: { password }
            });
            setFormMessage(message, "Hasło zostało zmienione, a stare sesje wylogowane.", "success");
            setTimeout(() => closeDialog(document.getElementById("employeePasswordDialog")), 900);
        } catch (error) {
            setFormMessage(message, error.message, "error");
        }
    });
    document.getElementById("todoEditorForm").addEventListener("submit", async event => {
        event.preventDefault();
        const message = document.getElementById("todoEditorMessage");
        const id = Number(document.getElementById("todoEditorId").value) || null;
        const editable = {
            fault_label: document.getElementById("todoFault").value,
            severity: document.getElementById("todoSeverity").value,
            status: document.getElementById("todoStatus").value,
            note: document.getElementById("todoNote").value
        };
        if (currentUser.role === "manager") editable.owner_id = Number(document.getElementById("todoOwner").value);
        try {
            if (id) {
                await apiRequest(`/api/todos/${id}`, { method: "PATCH", body: editable });
            } else {
                await apiRequest("/api/todos", {
                    method: "POST",
                    body: { ...todoEditorContext, ...editable, owner_id: editable.owner_id || currentUser.id }
                });
            }
            todoEditorContext = null;
            closeDialog(document.getElementById("todoEditorDialog"));
            await loadTodos();
            switchAppView("todo");
        } catch (error) {
            setFormMessage(message, error.message, "error");
        }
    });
    document.getElementById("employeeFilter").onchange = renderTodos;
    document.querySelectorAll("[data-todo-filter]").forEach(control => {
        control.onclick = () => {
            todoFilter = control.dataset.todoFilter;
            document.querySelectorAll("[data-todo-filter]").forEach(button => {
                const selected = button === control;
                button.classList.toggle("active", selected);
                button.setAttribute("aria-pressed", String(selected));
            });
            renderTodos();
        };
    });
    document.getElementById("refreshTodosButton").onclick = () => loadWorkspaceData().catch(error => window.alert(error.message));
    restoreSession();
}

initializeAccountsAndTodos();
