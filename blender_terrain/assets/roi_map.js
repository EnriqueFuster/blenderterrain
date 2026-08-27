"use strict";

const canvas = document.getElementById("map");
const context = canvas.getContext("2d");
const token = new URLSearchParams(location.search).get("token");
const state = { mode: null, zoom: 6, centerX: 0.49, centerY: 0.38, points: [], dragStart: null,
  pointer: null, panning: false, panOrigin: null, centerOrigin: null, tiles: new Map(),
  layers: [], activeLayer: null };

function lonToWorld(lon) { return (lon + 180) / 360; }
function latToWorld(lat) {
  const radians = Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI / 180;
  return (1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2;
}
function worldToLon(x) { return x * 360 - 180; }
function worldToLat(y) { return Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180 / Math.PI; }
function scale() { return 256 * 2 ** state.zoom; }
function screenToGeo(x, y) {
  return [worldToLon(state.centerX + (x - canvas.width / 2) / scale()),
    worldToLat(state.centerY + (y - canvas.height / 2) / scale())];
}
function geoToScreen(point) {
  return [(lonToWorld(point[0]) - state.centerX) * scale() + canvas.width / 2,
    (latToWorld(point[1]) - state.centerY) * scale() + canvas.height / 2];
}

function resize() {
  canvas.width = innerWidth * devicePixelRatio;
  canvas.height = innerHeight * devicePixelRatio;
  canvas.style.width = `${innerWidth}px`; canvas.style.height = `${innerHeight}px`;
  context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  canvas.width = innerWidth; canvas.height = innerHeight; draw();
}

function draw() {
  context.fillStyle = "#c9d2d8"; context.fillRect(0, 0, canvas.width, canvas.height);
  if (!state.activeLayer) return;
  const count = 2 ** state.zoom, tileSize = 256;
  const worldLeft = state.centerX - canvas.width / 2 / scale();
  const worldTop = state.centerY - canvas.height / 2 / scale();
  const firstX = Math.floor(worldLeft * count), firstY = Math.floor(worldTop * count);
  const lastX = Math.ceil((worldLeft + canvas.width / scale()) * count);
  const lastY = Math.ceil((worldTop + canvas.height / scale()) * count);
  for (let y = firstY; y <= lastY; y++) for (let x = firstX; x <= lastX; x++) {
    if (y < 0 || y >= count) continue;
    const wrappedX = ((x % count) + count) % count;
    const key = `${state.activeLayer.id}/${state.zoom}/${wrappedX}/${y}`;
    let image = state.tiles.get(key);
    if (!image) {
      image = new Image(); image.referrerPolicy = "no-referrer";
      image.onload = draw; image.src = state.activeLayer.url
        .replace("{z}", state.zoom).replace("{x}", wrappedX).replace("{y}", y);
      state.tiles.set(key, image);
    }
    if (image.complete && image.naturalWidth) {
      context.drawImage(image, (x / count - worldLeft) * scale(),
        (y / count - worldTop) * scale(), tileSize, tileSize);
    }
  }
  drawSelection();
}

function selectionPoints() {
  if (state.mode === "RECTANGLE" && state.points.length) {
    const a = state.points[0], b = state.pointer || state.points[1];
    if (!b) return state.points;
    return [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]];
  }
  return state.points;
}
function drawSelection() {
  const points = selectionPoints(); if (!points.length) return;
  context.beginPath();
  points.forEach((point, index) => { const [x, y] = geoToScreen(point);
    if (index) context.lineTo(x, y); else context.moveTo(x, y); });
  if ((state.mode === "RECTANGLE" && points.length === 4) ||
      (state.mode === "POLYGON" && points.length >= 3)) context.closePath();
  context.fillStyle = "#1687ff42"; context.strokeStyle = "#0769d7"; context.lineWidth = 3;
  context.fill(); context.stroke();
  for (const point of state.points) { const [x, y] = geoToScreen(point); context.beginPath();
    context.arc(x, y, 5, 0, Math.PI * 2); context.fillStyle = "white"; context.fill();
    context.strokeStyle = "#0769d7"; context.stroke(); }
}

function updateControls() {
  const valid = state.mode === "RECTANGLE" ? state.points.length === 2 : state.points.length >= 3;
  document.getElementById("use").disabled = !valid;
  document.getElementById("undo").disabled = !state.points.length;
  document.getElementById("clear").disabled = !state.points.length;
  document.getElementById("status").textContent = state.points.length
    ? `${state.points.length} point${state.points.length === 1 ? "" : "s"} selected` : "No area selected";
}

canvas.addEventListener("pointerdown", event => {
  if (event.shiftKey || event.button === 1 || event.button === 2) {
    state.panning = true; state.panOrigin = [event.clientX, event.clientY];
    state.centerOrigin = [state.centerX, state.centerY]; return;
  }
  state.dragStart = [event.clientX, event.clientY];
  if (state.mode === "RECTANGLE") { state.points = [screenToGeo(event.clientX, event.clientY)];
    state.pointer = state.points[0]; }
});
canvas.addEventListener("pointermove", event => {
  if (state.panning) { state.centerX = state.centerOrigin[0] - (event.clientX - state.panOrigin[0]) / scale();
    state.centerY = state.centerOrigin[1] - (event.clientY - state.panOrigin[1]) / scale(); draw(); return; }
  if (state.mode === "RECTANGLE" && state.dragStart) { state.pointer = screenToGeo(event.clientX, event.clientY); draw(); }
});
canvas.addEventListener("pointerup", event => {
  if (state.panning) { state.panning = false; return; }
  if (state.mode === "RECTANGLE" && state.dragStart) {
    const end = screenToGeo(event.clientX, event.clientY);
    if (Math.hypot(event.clientX - state.dragStart[0], event.clientY - state.dragStart[1]) > 4)
      state.points = [state.points[0], end];
    state.pointer = null; state.dragStart = null;
  } else if (state.mode === "POLYGON") state.points.push(screenToGeo(event.clientX, event.clientY));
  updateControls(); draw();
});
canvas.addEventListener("contextmenu", event => event.preventDefault());
canvas.addEventListener("wheel", event => { event.preventDefault(); zoom(event.deltaY < 0 ? 1 : -1); }, {passive: false});

function zoom(delta) { state.zoom = Math.max(3, Math.min(18, state.zoom + delta)); draw(); }
document.getElementById("zoom-in").onclick = () => zoom(1);
document.getElementById("zoom-out").onclick = () => zoom(-1);
document.getElementById("undo").onclick = () => { state.points.pop(); updateControls(); draw(); };
document.getElementById("clear").onclick = () => { state.points = []; state.pointer = null; updateControls(); draw(); };
document.getElementById("cancel").onclick = async () => { await fetch(`/cancel?token=${encodeURIComponent(token)}`, {method: "POST"}); window.close(); };
document.getElementById("use").onclick = async () => {
  let points = state.points;
  if (state.mode === "RECTANGLE") { const [a, b] = points;
    points = [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]]; }
  const ring = [...points, points[0]];
  const response = await fetch(`/result?token=${encodeURIComponent(token)}`, {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify({type: "Polygon", coordinates: [ring]})});
  const result = await response.json();
  if (result.ok) { document.getElementById("status").textContent = "Area sent to Blender. You can close this tab.";
    document.getElementById("use").disabled = true; setTimeout(() => window.close(), 600); }
  else document.getElementById("status").textContent = result.error;
};

fetch(`/config?token=${encodeURIComponent(token)}`).then(response => response.json()).then(config => {
  state.mode = config.mode;
  state.layers = config.layers;
  state.activeLayer = state.layers.find(layer => layer.id === config.default_layer);
  const selector = document.getElementById("base-layer");
  for (const layer of state.layers) {
    const option = document.createElement("option"); option.value = layer.id;
    option.textContent = layer.name; option.selected = layer.id === state.activeLayer.id;
    selector.appendChild(option);
  }
  selector.onchange = () => {
    state.activeLayer = state.layers.find(layer => layer.id === selector.value);
    updateAttribution(); draw();
  };
  updateAttribution();
  const bounds = config.bounds;
  state.centerX = lonToWorld((bounds.west + bounds.east) / 2);
  state.centerY = latToWorld((bounds.south + bounds.north) / 2);
  const span = Math.max(bounds.east - bounds.west, bounds.north - bounds.south);
  state.zoom = Math.max(3, Math.min(16, Math.floor(Math.log2(300 / Math.max(span, 0.001)))));
  document.getElementById("help").textContent = state.mode === "RECTANGLE"
    ? "Drag to draw a rectangle. Hold Shift and drag to pan; use the wheel to zoom."
    : "Click to add polygon vertices. Use Undo for the last point; hold Shift and drag to pan.";
  updateControls(); draw();
}).catch(() => { document.getElementById("status").textContent = "Cannot connect to Blender."; });
function updateAttribution() {
  const attribution = document.getElementById("attribution");
  attribution.textContent = state.activeLayer.attribution;
  attribution.href = state.activeLayer.attribution_url;
}
addEventListener("resize", resize); resize();
