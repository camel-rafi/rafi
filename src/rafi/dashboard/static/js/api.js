// 서버 API와 실시간 연결

(function (RaFi) {
  "use strict";

  const API = "/api/v1";
  const RECONNECT_MS = 3000;

  async function getJson(path) {
    const response = await fetch(`${API}${path}`);
    if (!response.ok) throw new Error(`${path}: ${response.status}`);
    return response.json();
  }

  async function loadSnapshot() {
    const [rooms, devices, alerts] = await Promise.all([
      getJson("/rooms"),
      getJson("/devices"),
      getJson("/alerts?limit=500"),
    ]);
    return { rooms, devices, alerts };
  }

  function getDevices() {
    return getJson("/devices");
  }

  // 서버가 받은 원본 메시지, 최근 것부터
  function getMessages(limit = 100) {
    return getJson(`/messages?limit=${limit}`);
  }

  async function postAck(alertId) {
    const response = await fetch(`${API}/alerts/${alertId}/ack`, { method: "POST" });
    if (!response.ok) throw new Error(`ack ${alertId}: ${response.status}`);
    return response.json();
  }

  // 끊기면 onClose를 부르고 RECONNECT_MS 뒤에 다시 연결한다
  function connectLive(handlers) {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${location.host}${API}/ws`);
    ws.onopen = () => handlers.onOpen(ws);
    ws.onmessage = (event) => handlers.onMessage(JSON.parse(event.data));
    ws.onclose = () => {
      handlers.onClose();
      setTimeout(() => connectLive(handlers), RECONNECT_MS);
    };
  }

  RaFi.api = { getJson, loadSnapshot, getDevices, getMessages, postAck, connectLive };
})((window.RaFi = window.RaFi || {}));
