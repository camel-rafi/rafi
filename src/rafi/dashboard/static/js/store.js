// 대시보드 상태
//
// 서버에서 받은 값은 여기에만 두고, 화면은 이 값을 읽어 그린다.
// 값이 바뀌면 구독한 쪽에 무엇이 바뀌었는지(change)를 알린다:
//   { kind: "all" }            처음 읽기·재연결로 전체를 새로 받음
//   { kind: "alert", id }      경보 하나가 생기거나 바뀜
//   { kind: "ack", id }        확인 처리 중·실패 표시가 바뀜
//   { kind: "devices" }        장비 상태가 바뀜
//   { kind: "connection" }     서버 연결 여부가 바뀜

(function (RaFi) {
  "use strict";

  const { byPriority, byRecentUpdate } = RaFi.dom;
  const { api } = RaFi;

  const state = {
    connected: false,
    loaded: false, // 처음 읽기를 마쳤는지. 그 전에는 "경보 없음" 대신 "-"를 보인다
    roomId: null,
    room: null,
    devices: new Map(),
    alerts: new Map(),
    acking: new Map(), // alert_id → "pending" | "failed"
  };

  const listeners = new Set();

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function notify(change) {
    for (const listener of listeners) listener(change);
  }

  function setSnapshot(rooms, devices, alerts) {
    // M2는 칸 하나만 표시한다
    [state.roomId, state.room] = Object.entries(rooms)[0] || [null, null];
    state.devices = new Map(devices.map((d) => [d.device_id, d]));
    state.alerts = new Map(alerts.map((a) => [a.alert_id, a]));
    for (const alertId of [...state.acking.keys()]) {
      const alert = state.alerts.get(alertId);
      if (!alert || alert.status !== "unacked") state.acking.delete(alertId);
    }
    state.loaded = true;
    notify({ kind: "all" });
  }

  function putAlert(alert) {
    state.alerts.set(alert.alert_id, alert);
    if (alert.status !== "unacked") state.acking.delete(alert.alert_id);
    notify({ kind: "alert", id: alert.alert_id });
  }

  function putDevice(device) {
    state.devices.set(device.device_id, device);
    notify({ kind: "devices" });
  }

  function setDevices(devices) {
    state.devices = new Map(devices.map((d) => [d.device_id, d]));
    notify({ kind: "devices" });
  }

  function setConnected(connected) {
    if (state.connected === connected) return;
    state.connected = connected;
    notify({ kind: "connection" });
  }

  function setAckState(alertId, value) {
    if (value) state.acking.set(alertId, value);
    else state.acking.delete(alertId);
    notify({ kind: "ack", id: alertId });
  }

  async function ack(alertId) {
    if (state.acking.get(alertId) === "pending") return;
    setAckState(alertId, "pending");
    try {
      putAlert(await api.postAck(alertId)); // 확인된 경보가 들어오면 처리 중 표시도 지운다
    } catch (error) {
      console.error(error);
      setAckState(alertId, "failed");
    }
  }

  function unackedAlerts() {
    return [...state.alerts.values()].filter((a) => a.status === "unacked").sort(byPriority);
  }

  function ackedAlerts() {
    return [...state.alerts.values()].filter((a) => a.status === "acked").sort(byRecentUpdate);
  }

  RaFi.store = {
    state,
    subscribe,
    setSnapshot,
    putAlert,
    putDevice,
    setDevices,
    setConnected,
    ack,
    unackedAlerts,
    ackedAlerts,
  };
})((window.RaFi = window.RaFi || {}));
