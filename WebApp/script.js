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

function drawCylinderGraph(canvas, source, state, { width = 220, height = 48 } = {}) {
    const values = Array.from({ length: 21 }, (_, index) => Number(source[`mV_${index}`]));
    if (values.some(value => !Number.isFinite(value))) return false;

    const colors = {
        ok: "#1ea97a",
        male: "#d99800",
        srednie: "#e37c00",
        duze: "#dc2626",
        unknown: "#7467dc"
    };
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const context = canvas.getContext("2d");
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const padding = 3;
    const color = colors[state] || colors.unknown;

    canvas.width = width * ratio;
    canvas.height = height * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    context.beginPath();
    values.forEach((value, index) => {
        const x = padding + (index / (values.length - 1)) * (width - padding * 2);
        const y = height - padding - ((value - min) / span) * (height - padding * 2);
        index === 0 ? context.moveTo(x, y) : context.lineTo(x, y);
    });
    context.lineTo(width - padding, height - padding);
    context.lineTo(padding, height - padding);
    context.closePath();
    const fill = context.createLinearGradient(0, 0, 0, height);
    fill.addColorStop(0, `${color}33`);
    fill.addColorStop(1, `${color}00`);
    context.fillStyle = fill;
    context.fill();

    context.beginPath();
    values.forEach((value, index) => {
        const x = padding + (index / (values.length - 1)) * (width - padding * 2);
        const y = height - padding - ((value - min) / span) * (height - padding * 2);
        index === 0 ? context.moveTo(x, y) : context.lineTo(x, y);
    });
    context.strokeStyle = color;
    context.lineWidth = 1.8;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.stroke();
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
                    const graphSection = document.createElement("section");
                    const graphTitle = document.createElement("h3");
                    const graph = document.createElement("canvas");
                    graphSection.className = "cylinder-details-section";
                    graphTitle.textContent = "Widmo akustyczne cylindra";
                    graph.className = "cylinder-details-graph";
                    graph.setAttribute("role", "img");
                    graph.setAttribute("aria-label", `Wykres widma akustycznego cylindra ${row.cylinder}`);
                    drawCylinderGraph(graph, source, state, { width: 640, height: 180 });
                    graphSection.append(graphTitle, graph);
                    details.append(graphSection, createParameterSection("Parametry pomiarowe", measurementParameters));
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

function renderEngineRanking(predictions) {
    const panel = document.getElementById("engineRankingPanel");
    const ranking = document.getElementById("engineRanking");
    const severityScore = { male: 1, srednie: 2, duze: 3, nie_dotyczy: 0 };
    const engines = [...new Set(predictions.map(row => row.engine_id))].map(engineId => {
        const cylinders = predictions.filter(row => row.engine_id === engineId);
        const faults = cylinders.filter(row => row.label !== "ok");
        const score = faults.reduce((sum, row) => sum + (severityScore[row.severity] || 1), 0);
        return { engineId, faultCount: faults.length, score };
    }).sort((first, second) =>
        first.score - second.score || first.faultCount - second.faultCount || first.engineId.localeCompare(second.engineId)
    );

    const best = engines[0];
    const worst = engines[engines.length - 1];
    ranking.replaceChildren();

    [
        { title: "✓ Najlepszy silnik", engine: best, className: "best" },
        { title: "⚠ Najgorszy silnik", engine: worst, className: "worst" }
    ].forEach(({ title, engine, className }) => {
        const card = document.createElement("div");
        const heading = document.createElement("strong");
        const description = document.createElement("span");
        card.className = `engine-rating ${className}`;
        heading.textContent = `${title}: ${engine.engineId}`;
        description.textContent = engine.faultCount === 0
            ? "Nie wykryto usterek."
            : `Wykryte usterki: ${engine.faultCount}; wskaźnik ryzyka: ${engine.score}.`;
        card.append(heading, description);
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
        renderEngineRanking(predictions);
        renderFaultChart(predictions);

        status.textContent = isValidationFile
            ? "Analiza danych z tabeli zakończona! Wynik został przygotowany."
            : "Predykcja zakończona! Wynik został przygotowany.";
    } catch (error) {
        console.error("ERROR:", error);
        status.textContent = "Błąd: " + error.message;
    }
});
