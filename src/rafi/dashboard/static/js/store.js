// 대시보드 상태
//
// 서버에서 받은 값은 여기에만 두고, 화면은 이 값을 읽어 그린다.
// 값이 바뀌면 구독한 쪽에 무엇이 바뀌었는지(change)를 알린다:
//   { kind: "all" }            처음 읽기·재연결로 전체를 새로 받음
//   { kind: "alert", id }      경보 하나가 생기거나 바뀜
//   { kind: "ack", id }        확인 처리 중·실패 표시가 바뀜
//   { kind: "devices" }        장비 상태가 바뀜
//   { kind: "connection" }     서버 연결 여부가 바뀜
//   { kind: "tick" }           시간이 흘렀음 (30초마다, 시각에 따라 달라지는 표시용)
//   { kind: "ongoing" }        진행 중인 경보가 바뀜 (사건이 끝났거나 새로 이어짐)
//
// 진행 중: 레이더는 사건 중 2초마다 갱신을 보낸다. 브라우저가 그 경보의 레이더 근거가 늘어난 갱신을
// 마지막으로 받은 지 ONGOING_MS 안이면 진행 중으로 본다. 브라우저 시계만 쓰므로 서버·레이더 PC와
// 시계가 어긋나도 틀리지 않는다. 대신 새로 고친 직후에는 다음 갱신이 와야 진행 중으로 보인다.
// (updated_at은 RF가 붙거나 확인 처리를 할 때도 바뀌어서 쓰지 않는다)

(function (RaFi) {
  "use strict";

  const { byPriority, byRecentUpdate } = RaFi.dom;
  const { api } = RaFi;

  const state = {
    connected: false,
    disconnectedAt: null, // 연결이 끊긴 시각. 화면이 언제 기준인지 알릴 때 쓴다
    loaded: false, // 처음 읽기를 마쳤는지. 그 전에는 "경보 없음" 대신 "-"를 보인다
    roomId: null,
    room: null,
    devices: new Map(),
    alerts: new Map(),
    acking: new Map(), // alert_id → "pending" | "failed"
  };

  const ONGOING_MS = 6000; // 2초 갱신을 두 번 놓치면 끝난 것으로 본다

  const radarSeenAt = new Map(); // alert_id → 레이더 갱신을 마지막으로 받은 브라우저 시각(ms)
  state.ongoing = new Set(); // 진행 중인 alert_id

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
    const previous = state.alerts.get(alert.alert_id);
    if (radarCount(alert) > radarCount(previous)) radarSeenAt.set(alert.alert_id, Date.now());
    state.alerts.set(alert.alert_id, alert);
    if (alert.status !== "unacked") state.acking.delete(alert.alert_id);
    updateOngoing();
    notify({ kind: "alert", id: alert.alert_id });
  }

  function radarCount(alert) {
    return alert ? alert.evidence.filter((e) => e.source === "radar").length : 0;
  }

  // 진행 중 목록을 다시 계산하고, 바뀌었으면 true
  function updateOngoing(now = Date.now()) {
    const next = new Set();
    for (const [alertId, seenAt] of radarSeenAt) {
      if (now - seenAt < ONGOING_MS) next.add(alertId);
      else radarSeenAt.delete(alertId);
    }
    const changed = next.size !== state.ongoing.size || [...next].some((id) => !state.ongoing.has(id));
    state.ongoing = next;
    return changed;
  }

  // 갱신이 끊긴 사건을 제때 끝난 것으로 바꾸려고 주기적으로 부른다 (바뀐 게 있을 때만 알린다)
  function checkOngoing() {
    if (updateOngoing()) notify({ kind: "ongoing" });
  }

  function isOngoing(alertId) {
    return state.ongoing.has(alertId);
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
    state.disconnectedAt = connected ? null : new Date().toISOString();
    notify({ kind: "connection" });
  }

  function tick() {
    notify({ kind: "tick" });
  }

  function setAckState(alertId, value) {
    if (value) state.acking.set(alertId, value);
    else state.acking.delete(alertId);
    notify({ kind: "ack", id: alertId });
  }

  async function ack(alertId) {
    if (state.acking.get(alertId) === "pending") return;
    if (!state.connected) return; // 끊긴 동안에는 버튼도 막혀 있다
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
    tick,
    checkOngoing,
    isOngoing,
    ack,
    unackedAlerts,
    ackedAlerts,
  };
})((window.RaFi = window.RaFi || {}));
