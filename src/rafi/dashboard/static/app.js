// RaFi 관리자 대시보드 시작점
//
// 연결 순서 (데이터 규칙 '대시보드 연동'):
//   1. WebSocket 연결
//   2. GET /rooms, /devices, /alerts로 현재 상태 읽기
//   3. 이후 변경은 WebSocket 메시지로 반영
//   4. 끊기면 화면에 표시하고 1번부터 다시
//
// 파일 구성
//   js/dom.js      화면 도우미, 등급·시각 표시, 목록 갱신(syncList)
//   js/api.js      서버 조회·확인 처리·WebSocket
//   js/store.js    상태 보관과 변경 알림
//   js/views/*.js  화면 (home 개요, alerts 경보 목록, detail 경보 상세, space 공간 보기)
//                  과 그리기 부품 (plan 평면도, room3d 칸 3D)
//   js/router.js   해시 주소 → 화면
//
// import(모듈) 대신 일반 script를 index.html에 적힌 순서대로 읽고 window.RaFi에 모은다.
// Windows에서는 레지스트리 설정에 따라 .js가 text/plain으로 제공될 수 있는데,
// 그러면 모듈 script는 브라우저가 실행하지 않기 때문이다.

(function (RaFi) {
  "use strict";

  const { api, store, router, views } = RaFi;
  const { element, hhmm } = RaFi.dom;

  const DEVICE_POLL_MS = 10000;
  const KIND = { radar: "레이더", rf: "RF" };

  function handleMessage(message) {
    if (message.type === "alert.created" || message.type === "alert.updated") {
      store.putAlert(message.payload);
    } else if (message.type === "device.status") {
      store.putDevice(message.payload);
    }
  }

  async function pollDevices() {
    // device.status는 연결 여부가 바뀔 때만 오므로, 마지막 수신 시각은 따로 읽어 온다
    try {
      store.setDevices(await api.getDevices());
    } catch {
      // 서버가 꺼져 있으면 WebSocket 쪽에서 끊김을 표시한다
    }
  }

  // ---------- 머리글 (모든 화면 공통) ----------

  function renderHeader(change) {
    const { state } = store;

    if (change.kind === "connection") {
      document.getElementById("connection").hidden = state.connected;
    }

    if (change.kind === "all" && state.room) {
      document.getElementById("room-name").textContent = `화장실 ${state.room.name}`;
      const windows = state.room.cleaning_windows.map((w) => `${hhmm(w.start)}~${hhmm(w.end)}`);
      document.getElementById("cleaning").textContent =
        windows.length ? `청소 일정 ${windows.join(" · ")}` : "청소 일정 없음";
    }

    if ((change.kind === "all" || change.kind === "devices") && state.room) {
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

    if (change.kind === "all" || change.kind === "alert") {
      const count = store.unackedAlerts().length;
      const badge = document.getElementById("nav-unacked");
      badge.textContent = count;
      badge.hidden = count === 0;
    }
  }

  function setActiveNav(routeName) {
    for (const navLink of document.querySelectorAll(".nav a")) {
      if (navLink.dataset.route === routeName) navLink.setAttribute("aria-current", "page");
      else navLink.removeAttribute("aria-current");
    }
  }

  // ---------- 시작 ----------

  const screen = router.create(
    document.getElementById("view"),
    [
      { name: "home", pattern: /^#\/?$/, view: views.home },
      { name: "alerts", pattern: /^#\/alerts\/?$/, view: views.alerts },
      { name: "alerts", pattern: /^#\/alerts\/(\d+)\/?$/, view: views.detail },
      { name: "space", pattern: /^#\/space(?:\/([\w-]+))?\/?$/, view: views.space },
    ],
    setActiveNav,
  );

  store.subscribe((change) => {
    renderHeader(change);
    screen.update(change);
  });

  api.connectLive({
    onOpen: async (ws) => {
      try {
        const { rooms, devices, alerts } = await api.loadSnapshot();
        store.setSnapshot(rooms, devices, alerts);
        store.setConnected(true);
      } catch (error) {
        console.error(error);
        ws.close();
      }
    },
    onMessage: handleMessage,
    onClose: () => store.setConnected(false),
  });

  setInterval(pollDevices, DEVICE_POLL_MS);
})((window.RaFi = window.RaFi || {}));
