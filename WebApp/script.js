console.log("NOWY JS");

const button = document.getElementById("uploadButton");
const previewButton = document.getElementById("previewButton");
const closePreviewButton = document.getElementById("closePreviewButton");
const MAX_FILE_SIZE = 10 * 1024 * 1024;
let resultUrl;

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
                context.strokeStyle = `rgba(22, 107, 77, ${0.12 * (1 - distance / connectionRange)})`;
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
            context.fillStyle = `rgba(${particle.color}, ${0.15 + influence * 0.3})`;
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

// Reference spectra averaged from tests/val.csv; zero readings are excluded.
const REFERENCE_SPECTRA = {
    ok: [36.787445, 32.688668, 26.86844, 31.771518, 40.64802, 47.821801, 53.257961, 56.891998, 58.878828, 59.543585, 57.486526, 53.836857, 49.045771, 43.904297, 39.403157, 34.583845, 31.419899, 28.906088, 24.821973, 26.264314, 28.614216],
    iglica: [37.548187, 35.665937, 26.584875, 32.16425, 39.826187, 45.209187, 44.471375, 47.310938, 46.420812, 44.045938, 44.674937, 43.910125, 42.285938, 36.843125, 31.96275, 30.743813, 30.082187, 24.829875, 17.45225, 16.226875, 18.552813],
    zakoksowany: [32.844167, 27.388111, 25.926556, 33.496722, 43.911944, 51.215833, 56.018222, 57.178944, 52.882556, 40.926222, 51.781, 57.251389, 56.310389, 52.131833, 49.055111, 46.637333, 45.045889, 41.938111, 39.211667, 38.533389, 38.273389],
    lejacy: [38.776786, 34.696714, 21.910357, 21.535571, 24.7555, 27.644643, 33.922214, 36.463643, 34.179571, 32.171286, 29.996929, 24.348643, 20.4665, 17.540643, 15.912143, 13.437571, 11.372214, 8.714, 6.687071, 5.9745, 5.663357],
    pompa: [37.910444, 32.172, 23.974222, 18.038333, 31.225889, 41.654222, 47.700222, 49.646111, 49.899556, 48.363556, 45.06, 39.479111, 33.042889, 27.405444, 25.748778, 24.084, 26.091111, 30.586778, 33.979667, 31.193333, 27.893556]
};

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

function drawCylinderGraph(canvas, source, state, {
    width = 220,
    height = 48,
    showScale = false,
    averageValues = null
} = {}) {
    const values = Array.from({ length: 21 }, (_, index) => Number(source[`mV_${index}`]));
    const plottedValues = [
        ...values,
        ...(averageValues || [])
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

    canvas.width = width * ratio;
    canvas.height = height * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    if (showScale) {
        context.font = "13px Arial, sans-serif";
        context.lineWidth = 1;
        context.strokeStyle = "rgba(22, 107, 77, 0.12)";
        context.fillStyle = "rgba(31, 41, 55, 0.68)";
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

    if (averageValues) {
        context.beginPath();
        let started = false;
        averageValues.forEach((value, index) => {
            if (!Number.isFinite(value) || value === 0) return;
            const x = padding.left + (index / (averageValues.length - 1)) * plotWidth;
            const y = height - padding.bottom - ((value - min) / span) * plotHeight;
            if (started) context.lineTo(x, y);
            else {
                context.moveTo(x, y);
                started = true;
            }
        });
        context.setLineDash([7, 5]);
        context.strokeStyle = "rgba(31, 41, 55, 0.72)";
        context.lineWidth = showScale ? 2 : 1.2;
        context.stroke();
        context.setLineDash([]);
    }
    return true;
}


function renderHealthPanel(predictions, sourceRows, isValidationFile = false) {
    const panel = document.getElementById("healthPanel");
    const engineSelect = document.getElementById("engineSelect");
    const summary = document.getElementById("healthSummary");
    const grid = document.getElementById("cylinderGrid");
    const details = document.getElementById("cylinderDetails");
    const sourceByCylinder = new Map(sourceRows.map(row => [`${row.engine_id}:${row.cylinder}`, row]));
    const engines = [...new Set(predictions.map(row => row.engine_id))];

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

        summary.textContent = faulty.length === 0
            ? `Silnik ${engineId} (${nCylinders} cylindrów): wszystkie cylindry są sprawne.`
            : `Silnik ${engineId} (${nCylinders} cylindrów): wykryto ${faulty.length} usterek${critical ? `, w tym ${critical} o dużym nasileniu` : ""}.`;
        grid.replaceChildren();
        details.style.display = "none";

        cylinders.forEach((row, index) => {
            const button = document.createElement("button");
            const state = row.label === "ok" ? "ok" : (row.label === "unknown" ? "unknown" : row.severity);
            const number = document.createElement("span");
            const label = document.createElement("span");
            const confidence = Number(row.confidence);
            button.type = "button";
            button.className = `cylinder ${state}`;
            button.style.animationDelay = `${index * 45}ms`;
            number.className = "cylinder-number";
            number.textContent = `Cylinder ${row.cylinder}`;
            label.className = "cylinder-label";
            label.textContent = labelNames[row.label] || row.label;
            button.append(number, label);
            const source = sourceByCylinder.get(`${row.engine_id}:${row.cylinder}`);
            if (source) {
                const skippedMeasurements = Array.from({ length: 21 }, (_, index) => Number(source[`mV_${index}`]))
                    .filter(value => !Number.isFinite(value) || value === 0).length;
                if (skippedMeasurements >= 3) {
                    const warning = document.createElement("span");
                    warning.className = "cylinder-warning";
                    warning.textContent = "!";
                    warning.title = `Pominięte pomiary: ${skippedMeasurements}`;
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
                confidenceLabel.textContent = `Pewność: ${(confidence * 100).toFixed(0)}%`;
                button.append(confidenceLabel);
            }
            button.addEventListener("click", () => {
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
                        const confidence = key === "confidence" ? Number(value) : null;
                        name.textContent = key === "confidence" ? "Pewność modelu" : key;
                        parameterValue.textContent = confidence != null && Number.isFinite(confidence)
                            ? `${(confidence * 100).toFixed(1)}%`
                            : (value === "" || value == null ? "—" : value);
                        item.append(name, parameterValue);
                        list.append(item);
                    });

                    section.className = "cylinder-details-section";
                    section.append(heading, list);
                    return section;
                };
                const title = document.createElement("h3");
                const prediction = { ...row };
                delete prediction.engine_id;
                delete prediction.cylinder;
                prediction.label = labelNames[row.label] || row.label;
                prediction.severity = severityNames[row.severity] || row.severity;

                title.className = "cylinder-details-title";
                title.textContent = `Cylinder ${row.cylinder} — wszystkie dostępne parametry`;
                details.replaceChildren(title);
                if (source) {
                    const measurementParameters = { ...source };
                    delete measurementParameters.label;
                    delete measurementParameters.severity;
                    measurementParameters["Pominięte pomiary"] = Array.from(
                        { length: 21 },
                        (_, index) => Number(source[`mV_${index}`])
                    ).filter(value => !Number.isFinite(value) || value === 0).length;
                    const graphSection = document.createElement("section");
                    const graphTitle = document.createElement("h3");
                    const graphLegend = document.createElement("p");
                    const graph = document.createElement("canvas");
                    const cylinderType = isValidationFile && source.label ? source.label : row.label;
                    const referenceSpectrum = REFERENCE_SPECTRA[cylinderType] || null;
                    graphSection.className = "cylinder-details-section";
                    graphTitle.textContent = "Widmo akustyczne cylindra";
                    graphLegend.className = "graph-legend";
                    graphLegend.textContent = `Linia przerywana: średnie widmo referencyjne typu „${labelNames[cylinderType] || cylinderType}” z val.csv.`;
                    graph.className = "cylinder-details-graph";
                    graph.setAttribute("role", "img");
                    graph.setAttribute("aria-label", `Wykres widma akustycznego cylindra ${row.cylinder}`);
                    graphSection.append(graphTitle);
                    if (referenceSpectrum) graphSection.append(graphLegend);
                    graphSection.append(graph);
                    details.append(graphSection, createParameterSection("Parametry pomiarowe", measurementParameters));
                    requestAnimationFrame(() => {
                        const graphBounds = graph.getBoundingClientRect();
                        drawCylinderGraph(graph, source, state, {
                            width: Math.round(graphBounds.width),
                            height: Math.round(graphBounds.height),
                            showScale: true,
                            averageValues: referenceSpectrum
                        });
                    });
                    if (isValidationFile) {
                        details.append(createParameterSection("Prawidłowy wynik z tabeli", {
                            label: labelNames[source.label] || source.label,
                            severity: severityNames[source.severity] || source.severity
                        }));
                    }
                }
                details.append(createParameterSection(
                    isValidationFile ? "Wynik modelu" : "Wynik predykcji",
                    prediction
                ));
                details.style.display = "block";
            });
            grid.append(button);
        });
    }

    engineSelect.onchange = displayEngine;
    panel.style.display = "block";
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
    const severityScore = { male: 1, srednie: 2, duze: 3, nie_dotyczy: 0 };
    const sourceByCylinder = new Map(sourceRows.map(row => [`${row.engine_id}:${row.cylinder}`, row]));
    const engines = [...new Set(predictions.map(row => row.engine_id))].map(engineId => {
        const cylinders = predictions.filter(row => row.engine_id === engineId);
        const faults = cylinders.filter(row => row.label !== "ok");
        const faultRisk = faults.reduce((sum, row) => sum + (severityScore[row.severity] || 1), 0);
        const skippedMeasurements = cylinders.reduce((sum, cylinder) => {
            const source = sourceByCylinder.get(`${cylinder.engine_id}:${cylinder.cylinder}`);
            if (!source) return sum;
            return sum + Array.from({ length: 21 }, (_, index) => Number(source[`mV_${index}`]))
                .filter(value => !Number.isFinite(value) || value === 0).length;
        }, 0);
        return {
            engineId,
            faultCount: faults.length,
            skippedMeasurements,
            score: faultRisk + skippedMeasurements
        };
    }).sort((first, second) =>
        first.score - second.score || first.faultCount - second.faultCount || first.engineId.localeCompare(second.engineId)
    );

    const worstEngines = engines.reverse().slice(0, 10);
    ranking.replaceChildren();
    details.style.display = "none";

    worstEngines.forEach((engine, index) => {
        const card = document.createElement("button");
        const rank = document.createElement("span");
        const content = document.createElement("div");
        const heading = document.createElement("strong");
        const description = document.createElement("span");
        const risk = document.createElement("span");
        card.type = "button";
        card.className = "engine-rating";
        rank.className = "engine-rank";
        rank.textContent = index + 1;
        heading.textContent = engine.engineId;
        description.textContent = `${engine.faultCount} usterek`;
        risk.className = "engine-risk";
        risk.textContent = `Ryzyko: ${engine.score}`;
        content.append(heading, description);
        card.append(rank, content, risk);
        card.addEventListener("click", () => {
            if (card.classList.contains("active")) {
                card.classList.remove("active");
                details.style.display = "none";
                return;
            }
            const cylinders = predictions.filter(row => row.engine_id === engine.engineId);
            const countBy = (rows, key, labels) => Object.entries(rows.reduce((counts, row) => {
                counts[row[key]] = (counts[row[key]] || 0) + 1;
                return counts;
            }, {})).map(([value, count]) => `${labels[value] || value}: ${count}`).join(", ") || "brak";
            const faults = cylinders.filter(row => row.label !== "ok");
            const faultTypes = new Set(faults.map(row => row.label));
            const severityFaults = faultTypes.size > 1
                ? faults.filter(row => row.severity !== "nie_dotyczy")
                : faults;
            const stats = {
                "Liczba cylindrów": cylinders.length,
                "Wykryte usterki": engine.faultCount,
                "Pominięte pomiary": engine.skippedMeasurements,
                "Wskaźnik ryzyka": engine.score,
                "Typy usterek": countBy(faults, "label", labelNames),
                "Nasilenie": countBy(severityFaults, "severity", severityNames)
            };
            const title = document.createElement("h3");
            const list = document.createElement("dl");
            title.className = "engine-ranking-details-title";
            title.textContent = `Statystyki silnika ${engine.engineId}`;
            list.className = "engine-stat-grid";
            Object.entries(stats).forEach(([name, value]) => {
                const item = document.createElement("div");
                const term = document.createElement("dt");
                const definition = document.createElement("dd");
                term.textContent = name;
                definition.textContent = value;
                item.append(term, definition);
                list.append(item);
            });
            ranking.querySelectorAll(".engine-rating").forEach(item => item.classList.remove("active"));
            card.classList.add("active");
            details.replaceChildren(title, list);
            card.after(details);
            details.style.display = "block";
        });
        ranking.append(card);
    });

    panel.style.display = "block";
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

    if (!file.name.toLowerCase().endsWith(".csv")) {
        status.textContent = "Podgląd jest dostępny tylko dla plików CSV.";
        previewPanel.style.display = "none";
        return;
    }

    if (file.size > MAX_FILE_SIZE) {
        status.textContent = "Plik musi być mniejszy niż 10 MB.";
        previewPanel.style.display = "none";
        return;
    }

    try {
        let text = await file.slice(0, 1000).text();
        if(file.length >= 1001){
            text.append("...")
        }
        preview.textContent = text || "Plik jest pusty.";
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
    console.log("CLICK");

    const input = document.getElementById("file");
    const status = document.getElementById("status");
    const download = document.getElementById("download");
    const healthPanel = document.getElementById("healthPanel");
    const engineRankingPanel = document.getElementById("engineRankingPanel");
    const faultChartPanel = document.getElementById("faultChartPanel");

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

    console.log("INPUT FILE:", file.name);

    const formData = new FormData();
    formData.append("file", file);

    status.textContent = "Przetwarzanie...";
    download.style.display = "none";
    healthPanel.style.display = "none";
    engineRankingPanel.style.display = "none";
    faultChartPanel.style.display = "none";

    try {
        const response = await fetch("http://127.0.0.1:8000/predict", {
            method: "POST",
            body: formData
        });

        console.log("RESPONSE STATUS:", response.status);

        if (!response.ok) {
            const errorBody = await response.text();
            try {
                const error = JSON.parse(errorBody);
                throw new Error(error.detail || errorBody);
            } catch (parseError) {
                if (parseError instanceof SyntaxError) {
                    throw new Error(errorBody);
                }
                throw parseError;
            }
        }

        const blob = await response.blob();
        console.log("RECEIVED SIZE:", blob.size);
        console.log("RECEIVED TYPE:", blob.type);

        const text = await blob.text();
        const [predictions, sourceRows] = await Promise.all([
            Promise.resolve(parseCsv(text)),
            file.name.toLowerCase().endsWith(".csv") ? file.text().then(parseCsv) : Promise.resolve([])
        ]);
        const isValidationFile = sourceRows.some(row =>
            Object.prototype.hasOwnProperty.call(row, "label")
            && Object.prototype.hasOwnProperty.call(row, "severity")
        );

        console.log("===== SERVER RESPONSE =====");
        console.log(text.substring(0, 500));
        console.log("===========================");

        const resultBlob = new Blob([text], { type: "text/csv;charset=utf-8" });
        if (resultUrl) URL.revokeObjectURL(resultUrl);
        const url = URL.createObjectURL(resultBlob);
        resultUrl = url;

        download.href = url;
        download.download = "wynik.csv";
        download.textContent = isValidationFile
            ? "⬇ Pobierz wynik"
            : "⬇ Pobierz wynik predykcji";
        download.style.display = "inline-flex";

        renderHealthPanel(predictions, sourceRows, isValidationFile);
        renderEngineRanking(predictions, sourceRows);
        renderFaultChart(predictions);

        status.textContent = isValidationFile
            ? "Analiza danych z tabeli zakończona! Wynik został przygotowany."
            : "Predykcja zakończona! Wynik został przygotowany.";
    } catch (error) {
        console.error("ERROR:", error);
        status.textContent = "Błąd: " + error.message;
    }
});
