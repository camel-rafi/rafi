// RaFi 관리자 대시보드
//
// 연결 순서 (데이터 규칙 '대시보드 연동'):
//   1. WebSocket 연결
//   2. GET /rooms, /devices, /alerts로 현재 상태 읽기
//   3. 이후 변경은 WebSocket 메시지로 반영
//   4. 끊기면 화면에 표시하고 1번부터 다시
// 모듈이 보낸 문자열(근거 등)은 textContent로만 넣어 HTML로 해석되지 않게 한다.

const API = "/api/v1";
const RECONNECT_MS = 3000;
const DEVICE_POLL_MS = 10000;
const ACKED_SHOWN = 20;
const SVG_NS = "http://www.w3.org/2000/svg";

const GRADE = {
  L2: { label: "L2 우선 확인", order: 0 },
  L1: { label: "L1 확인 요청", order: 1 },
  L3: { label: "L3 알림", order: 2 },
};
const SOURCE = { radar: "레이더", rf: "RF" };
const KIND = { radar: "레이더", rf: "RF" };
// 위에서 보면 칸 대부분을 덮는 구역. 평소에는 그리지 않고, 경보가 있으면 칸 테두리로 강조한다
const WIDE_ZONES = new Set(["high", "stall"]);

const state = {
  roomId: null,
  room: null,
  devices: new Map(),
  alerts: new Map(),
};

// ---------- 연결 ----------

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}${API}/ws`);

  ws.onopen = async () => {
    try {
      await loadAll();
      setConnected(true);
    } catch (error) {
      console.error(error);
      ws.close();
    }
  };
  ws.onmessage = (event) => handleMessage(JSON.parse(event.data));
  ws.onclose = () => {
    setConnected(false);
    setTimeout(connect, RECONNECT_MS);
  };
}

async function getJson(path) {
  const response = await fetch(`${API}${path}`);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

async function loadAll() {
  const [rooms, devices, alerts] = await Promise.all([
    getJson("/rooms"),
    getJson("/devices"),
    getJson("/alerts?limit=500"),
  ]);
  // M2는 칸 하나만 표시한다
  [state.roomId, state.room] = Object.entries(rooms)[0];
  state.devices = new Map(devices.map((d) => [d.device_id, d]));
  state.alerts = new Map(alerts.map((a) => [a.alert_id, a]));
  renderAll();
}

async function pollDevices() {
  try {
    const devices = await getJson("/devices");
    state.devices = new Map(devices.map((d) => [d.device_id, d]));
    renderHeader();
    renderSummary();
  } catch {
    // 서버가 꺼져 있으면 WebSocket 쪽에서 끊김을 표시한다
  }
}

function handleMessage(message) {
  if (message.type === "alert.created" || message.type === "alert.updated") {
    state.alerts.set(message.payload.alert_id, message.payload);
    renderSummary();
    renderPlan();
    renderAlerts();
  } else if (message.type === "device.status") {
    state.devices.set(message.payload.device_id, message.payload);
    renderHeader();
    renderSummary();
  }
}

function setConnected(connected) {
  document.getElementById("connection").hidden = connected;
}

// ---------- 확인 처리 ----------

async function ack(alertId, button, errorBox) {
  button.disabled = true;
  errorBox.textContent = "";
  try {
    const response = await fetch(`${API}/alerts/${alertId}/ack`, { method: "POST" });
    if (!response.ok) throw new Error(response.status);
    state.alerts.set(alertId, await response.json());
    renderSummary();
    renderPlan();
    renderAlerts();
  } catch {
    button.disabled = false;
    errorBox.textContent = "확인 처리에 실패했습니다. 다시 눌러 주세요.";
  }
}

// ---------- 그리기 ----------

function renderAll() {
  renderHeader();
  renderSummary();
  renderPlan();
  renderAlerts();
}

function renderHeader() {
  if (!state.room) return;
  document.getElementById("room-name").textContent = `화장실 ${state.room.name}`;

  const windows = state.room.cleaning_windows.map((w) => `${hhmm(w.start)}~${hhmm(w.end)}`);
  document.getElementById("cleaning").textContent =
    windows.length ? `청소 일정 ${windows.join(" · ")}` : "청소 일정 없음";

  const box = document.getElementById("devices");
  box.replaceChildren();
  for (const deviceId of Object.keys(state.room.devices)) {
    const device = state.devices.get(deviceId);
    const online = Boolean(device && device.online);
    const chip = element("span", `chip ${online ? "ok" : "bad"}`);
    chip.append(element("span", "dot"));
    chip.append(`${KIND[device ? device.kind : ""] || deviceId} ${online ? "연결" : "끊김"}`);
    if (device && device.error) chip.title = device.error;
    box.append(chip);
  }
}

function renderSummary() {
  const alerts = [...state.alerts.values()];
  const unacked = alerts.filter((a) => a.status === "unacked").length;
  const today = alerts.filter((a) => isToday(a.started_at));
  const cleaning = today.filter((a) => a.in_cleaning_window).length;

  document.getElementById("unacked-count").textContent = unacked;
  document.getElementById("today-count").textContent = today.length;
  document.getElementById("today-cleaning").textContent = cleaning ? `(청소 시간대 ${cleaning})` : "";

  const seen = [...state.devices.values()].map((d) => d.last_seen).filter(Boolean).sort();
  document.getElementById("last-seen").textContent = seen.length ? clock(seen.at(-1)) : "-";
}

function renderPlan() {
  const room = state.room;
  if (!room) return;
  const svg = document.getElementById("plan");
  const margin = 12;
  const { x: width, y: depth } = room.size_cm;
  // 좌표는 cm 그대로 쓴다. y는 뒷벽(위)에서 문(아래)으로 커지므로 SVG 방향과 같다
  svg.setAttribute("viewBox", `${-margin} ${-margin} ${width + margin * 2} ${depth + margin * 2}`);
  svg.replaceChildren();

  const grades = activeGradeByRoi();
  const wide = [...grades.entries()].filter(([roi]) => WIDE_ZONES.has(roi));
  const wideGrade = highestGrade(wide.map(([, grade]) => grade));

  svg.append(rect(0, 0, width, depth, `room ${wideGrade || ""}`));

  const activeZones = [...grades.keys()]
    .filter((roi) => !WIDE_ZONES.has(roi) && room.zones[roi])
    .map((roi) => room.zones[roi]);
  for (const fixture of Object.values(room.fixtures)) {
    // 경보가 뜬 구역과 겹치는 기물은 이름을 숨겨 구역 이름이 읽히게 한다
    const covered = activeZones.some((zone) => overlaps(zone, fixture));
    drawArea(svg, fixture, "fixture", covered ? null : fixture.name);
  }
  for (const [zoneId, zone] of Object.entries(room.zones)) {
    if (WIDE_ZONES.has(zoneId)) continue;
    const grade = grades.get(zoneId);
    drawArea(svg, zone, `zone ${grade || ""}`, grade ? zone.name : null, "zone-label");
  }

  for (const [deviceId, device] of Object.entries(room.devices)) {
    if (!device.position_cm) continue;
    const { x, y } = device.position_cm;
    const dot = document.createElementNS(SVG_NS, "circle");
    dot.setAttribute("cx", x);
    dot.setAttribute("cy", y);
    dot.setAttribute("r", 2.2);
    dot.setAttribute("class", "radar");
    svg.append(dot, text(x, y - 3.5, KIND[device.kind] || deviceId));
  }

  // 위치가 넓은 구역(높은 곳, 칸 전체)의 경보는 칸 위에 글자로 알린다
  wide.forEach(([roi, grade], index) => {
    const label = text(width / 2, -4 - index * 6, `${room.zones[roi].name} · ${GRADE[grade].label}`);
    label.setAttribute("class", `room-alert ${grade}`);
    svg.append(label);
  });

  svg.append(text(width / 2, depth + 8, "출입문"));
}

function drawArea(svg, area, className, label, labelClass) {
  const [x0, x1] = area.x_cm;
  const [y0, y1] = area.y_cm;
  if (y0 === y1 || x0 === x1) {
    // 두께를 모르는 면(문 등)은 선으로 그린다
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", x0);
    line.setAttribute("y1", y0);
    line.setAttribute("x2", x1);
    line.setAttribute("y2", y1);
    line.setAttribute("class", "fixture-line");
    svg.append(line);
    return;
  }
  svg.append(rect(x0, y0, x1 - x0, y1 - y0, className));
  if (label) {
    const t = text((x0 + x1) / 2, (y0 + y1) / 2 + 1.5, label);
    if (labelClass) t.setAttribute("class", labelClass);
    svg.append(t);
  }
}

function renderAlerts() {
  const list = document.getElementById("alerts");
  const alerts = [...state.alerts.values()];
  const unacked = alerts.filter((a) => a.status === "unacked").sort(byPriority);
  const acked = alerts
    .filter((a) => a.status === "acked")
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, ACKED_SHOWN);

  list.replaceChildren(...[...unacked, ...acked].map(alertItem));
  document.getElementById("no-alerts").hidden = alerts.length > 0;
}

function alertItem(alert) {
  const item = element("li", `alert ${alert.status}`);

  const head = element("div", "head");
  head.append(element("span", `badge ${alert.grade}`, GRADE[alert.grade].label));
  head.append(element("span", "where", where(alert)));
  if (alert.in_cleaning_window) head.append(element("span", "tag", "청소 시간대"));
  head.append(element("span", "time", clock(alert.started_at)));
  item.append(head);

  const evidence = element("ul", "evidence");
  for (const e of alert.evidence) {
    const parts = [`${SOURCE[e.source]} ${e.score.toFixed(2)}`, ...e.reasons];
    evidence.append(element("li", "", parts.join(" · ")));
  }
  item.append(evidence);

  const actions = element("div", "actions");
  if (alert.status === "unacked") {
    const button = element("button", "", "확인 처리");
    const errorBox = element("span", "error");
    button.addEventListener("click", () => ack(alert.alert_id, button, errorBox));
    actions.append(button, errorBox);
  } else {
    actions.append(element("span", "done", `✓ 확인됨 ${clock(alert.acked_at)}`));
  }
  item.append(actions);
  return item;
}

// ---------- 도우미 ----------

function where(alert) {
  if (!alert.roi) return `${state.room ? state.room.name : alert.room} · 위치 미상`;
  const zone = state.room && state.room.zones[alert.roi];
  return zone ? zone.name : alert.roi;
}

function activeGradeByRoi() {
  const grades = new Map();
  for (const alert of state.alerts.values()) {
    if (alert.status !== "unacked" || !alert.roi) continue;
    grades.set(alert.roi, highestGrade([grades.get(alert.roi), alert.grade]));
  }
  return grades;
}

function overlaps(a, b) {
  return (
    a.x_cm[0] < b.x_cm[1] && b.x_cm[0] < a.x_cm[1] &&
    a.y_cm[0] < b.y_cm[1] && b.y_cm[0] < a.y_cm[1]
  );
}

function highestGrade(grades) {
  const valid = grades.filter(Boolean).sort((a, b) => GRADE[a].order - GRADE[b].order);
  return valid[0] || null;
}

function byPriority(a, b) {
  return GRADE[a.grade].order - GRADE[b.grade].order || b.started_at.localeCompare(a.started_at);
}

function isToday(iso) {
  return new Date(iso).toDateString() === new Date().toDateString();
}

function clock(iso) {
  const time = new Date(iso);
  return [time.getHours(), time.getMinutes(), time.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

function hhmm(time) {
  return time.slice(0, 5);
}

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

function rect(x, y, width, height, className) {
  const node = document.createElementNS(SVG_NS, "rect");
  node.setAttribute("x", x);
  node.setAttribute("y", y);
  node.setAttribute("width", width);
  node.setAttribute("height", height);
  node.setAttribute("rx", 1);
  node.setAttribute("class", className);
  return node;
}

function text(x, y, content) {
  const node = document.createElementNS(SVG_NS, "text");
  node.setAttribute("x", x);
  node.setAttribute("y", y);
  node.setAttribute("text-anchor", "middle");
  node.textContent = content;
  return node;
}

connect();
setInterval(pollDevices, DEVICE_POLL_MS);
