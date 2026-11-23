// 1 · 경보 목록: 기간·등급·구역·상태로 찾고, 날짜별로 묶어 보며 확인 처리한다
//
// 거르는 범위는 처음에 불러온 최근 경보(최대 500건)와 그 뒤 실시간으로 받은 경보다.
// 더 오래된 경보까지 찾으려면 서버 /alerts에 기간 조건이 필요하다.

(function (RaFi) {
  "use strict";

  const { element, link, where, gradeBadge, evidenceList, ackControls, syncList, timeLabel, isToday, dayKey, dayLabel, byPriority, GRADE } = RaFi.dom;
  const { store } = RaFi;

  const LOAD_LIMIT = 500; // api.js의 처음 읽기와 같은 값
  const PERIODS = [
    ["today", "오늘"],
    ["week", "최근 7일"],
    ["all", "불러온 전체"],
  ];
  const STATUSES = [
    ["all", "전체"],
    ["unacked", "미확인"],
    ["acked", "확인됨"],
  ];
  const NO_ROI = "none";

  // 다른 화면에 다녀와도 고른 조건이 남도록 화면 밖에 둔다
  const filters = {
    period: "week",
    status: "all",
    grades: new Set(Object.keys(GRADE)),
    roi: "",
    skipCleaning: false,
  };

  function mount(root) {
    const crumbs = element("p", "crumbs");
    crumbs.append(link("#/", "", "개요"), " › 경보 목록");
    const head = element("div", "page-head");
    head.append(element("h1", "", "경보 목록"));

    // ---------- 찾기 조건 ----------
    const controls = element("section", "panel filters");
    controls.setAttribute("aria-label", "찾기 조건");

    const periodButtons = segmented(controls, "기간", PERIODS, () => filters.period, (id) => {
      filters.period = id;
      render();
    });
    const statusButtons = segmented(controls, "상태", STATUSES, () => filters.status, (id) => {
      filters.status = id;
      render();
    });

    const gradeBox = element("fieldset", "field grades");
    gradeBox.append(element("legend", "", "등급"));
    for (const grade of Object.keys(GRADE)) {
      const label = element("label", "check");
      const input = element("input");
      input.type = "checkbox";
      input.checked = filters.grades.has(grade);
      input.addEventListener("change", () => {
        if (input.checked) filters.grades.add(grade);
        else filters.grades.delete(grade);
        render();
      });
      label.append(input, grade);
      gradeBox.append(label);
    }
    controls.append(gradeBox);

    const roiLabel = element("label", "field");
    const roiSelect = element("select");
    roiSelect.addEventListener("change", () => {
      filters.roi = roiSelect.value;
      render();
    });
    roiLabel.append(element("span", "field-name", "구역"), roiSelect);
    controls.append(roiLabel);

    const cleaningLabel = element("label", "check field-check");
    const cleaningInput = element("input");
    cleaningInput.type = "checkbox";
    cleaningInput.checked = filters.skipCleaning;
    cleaningInput.addEventListener("change", () => {
      filters.skipCleaning = cleaningInput.checked;
      render();
    });
    cleaningLabel.append(cleaningInput, "청소 시간대 제외");
    controls.append(cleaningLabel);

    // ---------- 목록 ----------
    const list = element("ol", "alerts");
    const empty = element("p", "muted empty");
    const footer = element("p", "muted small");
    const panel = element("section", "panel");
    panel.append(list, empty, footer);
    root.append(crumbs, head, controls, panel);

    let roiOptionsFor = null;

    function renderRoiOptions() {
      const { room } = store.state;
      if (!room || roiOptionsFor === room) return;
      roiOptionsFor = room;
      const options = [["", "모든 구역"]];
      for (const [roi, zone] of Object.entries(room.zones)) options.push([roi, zone.name]);
      options.push([NO_ROI, "위치 미상 (RF만)"]);
      roiSelect.replaceChildren(
        ...options.map(([value, text]) => {
          const option = element("option", "", text);
          option.value = value;
          return option;
        }),
      );
      roiSelect.value = filters.roi;
    }

    function render() {
      const { state } = store;
      renderRoiOptions();

      const all = [...state.alerts.values()];
      const base = all.filter(matchesExceptStatus);
      const counts = {
        all: base.length,
        unacked: base.filter((a) => a.status === "unacked").length,
        acked: base.filter((a) => a.status === "acked").length,
      };
      updateSegmented(periodButtons, () => filters.period);
      updateSegmented(statusButtons, () => filters.status, state.loaded ? counts : null);

      const shown = base.filter((a) => filters.status === "all" || a.status === filters.status);
      syncList(list, withDayHeads(shown), {
        key: (item) => (item.day ? `day-${item.day}` : item.alert_id),
        sig: (item) =>
          item.day ? `${item.label}|${item.count}` : `${item.updated_at}|${item.status}|${state.acking.get(item.alert_id) || ""}`,
        build: (item) => (item.day ? dayHead(item) : alertItem(item)),
      });

      empty.hidden = !state.loaded || shown.length > 0;
      empty.textContent = "조건에 맞는 경보가 없습니다.";
      footer.textContent = state.loaded
        ? `${shown.length}건 · 최근 경보 ${all.length}건 안에서 찾았습니다` +
          (all.length >= LOAD_LIMIT ? ". 더 오래된 경보는 아직 이 화면에서 볼 수 없습니다." : "")
        : "불러오는 중…";
    }

    function update(change) {
      if (change.kind === "devices" || change.kind === "connection" || change.kind === "tick") return;
      render();
    }

    render();
    return { update };
  }

  // ---------- 거르기·묶기 ----------

  function matchesExceptStatus(alert) {
    if (filters.period === "today" && !isToday(alert.started_at)) return false;
    if (filters.period === "week" && new Date(alert.started_at) < weekStart()) return false;
    if (!filters.grades.has(alert.grade)) return false;
    if (filters.roi === NO_ROI && alert.roi) return false;
    if (filters.roi && filters.roi !== NO_ROI && alert.roi !== filters.roi) return false;
    if (filters.skipCleaning && alert.in_cleaning_window) return false;
    return true;
  }

  // 오늘을 포함한 7일의 첫날 0시
  function weekStart() {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - 6);
    return start;
  }

  // 날짜별로 묶는다 (최근 날짜부터). 하루 안에서는 미확인을 등급순으로 먼저, 확인된 것은 최근부터
  function withDayHeads(alerts) {
    const days = new Map();
    for (const alert of alerts) {
      const day = dayKey(alert.started_at);
      if (!days.has(day)) days.set(day, []);
      days.get(day).push(alert);
    }
    const items = [];
    for (const day of [...days.keys()].sort().reverse()) {
      const group = days.get(day);
      const unacked = group.filter((a) => a.status === "unacked").sort(byPriority);
      const acked = group.filter((a) => a.status !== "unacked").sort((a, b) => b.started_at.localeCompare(a.started_at));
      items.push({ day, label: dayLabel(group[0].started_at), count: group.length }, ...unacked, ...acked);
    }
    return items;
  }

  // ---------- 그리기 ----------

  function segmented(parent, name, options, current, onPick) {
    const box = element("div", "field");
    box.append(element("span", "field-name", name));
    const group = element("div", "segmented");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", name);
    const buttons = options.map(([id, text]) => {
      const button = element("button", "", text);
      button.type = "button";
      button.dataset.id = id;
      button.dataset.text = text;
      button.addEventListener("click", () => onPick(id));
      group.append(button);
      return button;
    });
    box.append(group);
    parent.append(box);
    updateSegmented(buttons, current);
    return buttons;
  }

  function updateSegmented(buttons, current, counts = null) {
    for (const button of buttons) {
      const { id, text } = button.dataset;
      button.textContent = counts ? `${text} ${counts[id]}` : text;
      button.setAttribute("aria-pressed", String(id === current()));
    }
  }

  function dayHead(item) {
    const head = element("li", "day-head");
    head.append(element("span", "", item.label), element("span", "muted", `${item.count}건`));
    return head;
  }

  function alertItem(alert) {
    const { state } = store;
    const item = element("li", `alert ${alert.status}`);

    const head = element("div", "head");
    head.append(gradeBadge(alert.grade), link(`#/alerts/${alert.alert_id}`, "where", where(alert, state.room)));
    if (alert.in_cleaning_window) head.append(element("span", "tag", "청소 시간대"));
    head.append(timeLabel(alert.started_at));

    item.append(head, evidenceList(alert), ackControls(alert, state.acking.get(alert.alert_id), store.ack));
    return item;
  }

  // 개요의 등급 표시등에서 들어올 때: 그 등급의 미확인 경보만 보이게 조건을 맞춘다 (화면이 열리기 전에 부른다)
  function showGrade(grade) {
    filters.period = "all";
    filters.status = "unacked";
    filters.grades = new Set([grade]);
    filters.roi = "";
    filters.skipCleaning = false;
  }

  RaFi.views = RaFi.views || {};
  RaFi.views.alerts = { mount, showGrade };
})((window.RaFi = window.RaFi || {}));
