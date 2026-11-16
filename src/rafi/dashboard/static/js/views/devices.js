// 장비·수신 기록: 장비별 지금 상태와 서버가 받은 원본 메시지
//
// 장비 상태는 저장소 값(실시간 알림 + 10초마다 조회)을 쓰고,
// 수신 메시지는 이 화면을 보는 동안만 10초마다 /messages로 다시 읽는다.
// 연결 이력은 서버가 아직 저장하지 않으므로(장비 상태는 메모리에만 있음) 지금 상태만 보여 준다.

(function (RaFi) {
  "use strict";

  const { element, link, syncList, timeLabel } = RaFi.dom;
  const { store, api } = RaFi;

  const REFRESH_MS = 10000;
  const MESSAGE_LIMIT = 100;
  const KIND = { radar: "레이더", rf: "RF" };
  const TYPES = ["radar.detection", "radar.status", "radar.occupancy", "rf.window", "rf.status"];

  function mount(root) {
    const filters = { type: "", device: "" };
    const opened = new Set(); // JSON을 펼친 메시지 (다시 읽어도 유지)
    let messages = [];
    let loadError = null;
    let loaded = false;

    const crumbs = element("p", "crumbs");
    crumbs.append(link("#/", "", "개요"), " › 장비·수신 기록");
    const head = element("div", "page-head");
    head.append(element("h1", "", "장비·수신 기록"));

    // ---------- 장비 ----------
    const cards = element("section", "device-cards");
    cards.setAttribute("aria-label", "장비 상태");
    const historyNote = element("p", "muted small", "연결 이력은 아직 저장하지 않아 지금 상태만 보여 줍니다. 상태 메시지가 30초 동안 없으면 끊김으로 봅니다.");

    // ---------- 수신 메시지 ----------
    const panel = element("section", "panel");
    const panelHead = element("div", "panel-head");
    const tools = element("div", "tools");
    const typeSelect = select([["", "모든 종류"], ...TYPES.map((t) => [t, t])], (value) => {
      filters.type = value;
      renderMessages();
    });
    const deviceSelect = select([["", "모든 장비"]], (value) => {
      filters.device = value;
      renderMessages();
    });
    const refresh = element("button", "", "새로 읽기");
    refresh.type = "button";
    refresh.addEventListener("click", load);
    tools.append(labeled("종류", typeSelect), labeled("장비", deviceSelect), refresh);
    panelHead.append(element("h2", "", "수신 메시지"), tools);
    const status = element("p", "muted small");
    const list = element("ol", "messages");
    panel.append(panelHead, status, list);

    root.append(crumbs, head, cards, historyNote, panel);

    async function load() {
      try {
        messages = await api.getMessages(MESSAGE_LIMIT);
        loadError = null;
      } catch (error) {
        console.error(error);
        loadError = "수신 기록을 불러오지 못했습니다. 서버 연결을 확인해 주세요.";
      }
      loaded = true;
      renderMessages();
    }

    function renderCards() {
      const { state } = store;
      if (!state.room) {
        cards.replaceChildren(element("p", "muted", "불러오는 중…"));
        return;
      }
      const ids = Object.keys(state.room.devices);
      cards.replaceChildren(...ids.map((id) => deviceCard(id, state.room.devices[id], state.devices.get(id))));

      const current = deviceSelect.value;
      deviceSelect.replaceChildren(
        ...[["", "모든 장비"], ...ids.map((id) => [id, id])].map(([value, text]) => option(value, text)),
      );
      deviceSelect.value = current;
    }

    function renderMessages() {
      if (!loaded) {
        status.textContent = "불러오는 중…";
        return;
      }
      const shown = messages.filter(
        ({ message }) =>
          (!filters.type || message.type === filters.type) && (!filters.device || message.device_id === filters.device),
      );
      status.textContent = loadError || `최근 ${messages.length}건 중 ${shown.length}건 · 10초마다 다시 읽습니다`;
      syncList(list, shown, {
        key: (entry) => entry.message.message_id,
        sig: (entry) => String(opened.has(entry.message.message_id)),
        build: messageItem,
      });
    }

    function messageItem({ received_at: receivedAt, message }) {
      const id = message.message_id;
      const item = element("li", "message");
      const row = element("div", "message-row");
      const toggle = element("button", "", opened.has(id) ? "접기" : "JSON");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(opened.has(id)));
      toggle.addEventListener("click", () => {
        if (opened.has(id)) opened.delete(id);
        else opened.add(id);
        renderMessages();
      });
      row.append(
        timeLabel(receivedAt),
        element("code", "type", message.type),
        element("span", "device", message.device_id),
        element("span", "summary", summarize(message)),
        toggle,
      );
      item.append(row);
      if (opened.has(id)) item.append(element("pre", "json", JSON.stringify(message, null, 2)));
      return item;
    }

    function update(change) {
      if (change.kind === "all" || change.kind === "devices") renderCards();
    }

    const timer = setInterval(load, REFRESH_MS);
    renderCards();
    renderMessages();
    load();

    return {
      update,
      unmount() {
        clearInterval(timer);
      },
    };
  }

  function deviceCard(id, config, status) {
    const online = Boolean(status && status.online);
    const card = element("article", `card device ${online ? "ok" : "bad"}`);
    const top = element("div", "device-top");
    const chip = element("span", `chip ${online ? "ok" : "bad"}`);
    chip.append(element("span", "dot"), online ? "연결" : "끊김");
    top.append(element("h2", "", `${KIND[config.kind] || config.kind} · ${id}`), chip);

    const facts = element("dl", "facts");
    const add = (name, value) => facts.append(element("dt", "", name), value instanceof Node ? wrap(value) : element("dd", "", value));
    add("마지막 수신", status && status.last_seen ? timeLabel(status.last_seen, "") : "없음");
    add("오류", (status && status.error) || "없음");
    const p = config.position_cm;
    add("설치 위치", p ? `x ${p.x} · y ${p.y} · 높이 ${p.z} cm` : "평면도 위치 없음");
    card.append(top, facts);
    return card;
  }

  function wrap(node) {
    const dd = element("dd");
    dd.append(node);
    return dd;
  }

  // 메시지 한 줄 요약. 모듈이 보낸 문자열은 textContent로만 들어간다
  function summarize(message) {
    const p = message.payload || {};
    switch (message.type) {
      case "radar.detection":
        return [`${p.roi} · 점수 ${Number(p.score).toFixed(2)}`, ...(p.reasons || [])].join(" · ") +
          (p.event_id ? ` · 사건 …${String(p.event_id).slice(-6)}` : "");
      case "radar.status":
        return p.sensor_ok ? `정상 · ${p.fps} fps` : `오류 · ${p.error || "이유 없음"}`;
      case "rf.status":
        return p.sensor_ok ? `정상 · 채널 ${p.channel} · 버린 프레임 ${p.dropped_frames}` : `오류 · ${p.error || "이유 없음"}`;
      case "radar.occupancy":
        return `사람 ${p.person_count}명` + (p.zones && p.zones.length ? ` · ${p.zones.join(", ")}` : "");
      case "rf.window": {
        const devices = p.devices || [];
        const top = Math.max(0, ...devices.map((d) => d.bytes_up / (p.window_sec || 1)));
        return `기기 ${devices.length}대 · 업링크 최대 ${Math.round(top).toLocaleString("ko-KR")} B/s`;
      }
      default:
        return JSON.stringify(p).slice(0, 120);
    }
  }

  function select(options, onChange) {
    const node = element("select");
    node.replaceChildren(...options.map(([value, text]) => option(value, text)));
    node.addEventListener("change", () => onChange(node.value));
    return node;
  }

  function option(value, text) {
    const node = element("option", "", text);
    node.value = value;
    return node;
  }

  function labeled(name, control) {
    const label = element("label", "field inline");
    label.append(element("span", "field-name", name), control);
    return label;
  }

  RaFi.views = RaFi.views || {};
  RaFi.views.devices = { mount };
})((window.RaFi = window.RaFi || {}));
