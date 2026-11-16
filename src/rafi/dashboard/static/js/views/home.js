// 0 · 개요: 지금 칸 상태와 확인할 경보

(function (RaFi) {
  "use strict";

  const { element, link, clock, isToday, where, gradeBadge, ackControls, syncList, timeLabel, GRADE } = RaFi.dom;
  const { store, plan } = RaFi;

  const DAY_MINUTES = 24 * 60;

  function mount(root) {
    const summary = element("section", "summary");
    const unackedValue = summaryCard(summary, "미확인 경보");
    const todayValue = summaryCard(summary, "오늘 경보");
    const todayCount = element("span", "", "-");
    const todaySub = element("span", "sub");
    todayValue.replaceChildren(todayCount, " ", todaySub);
    const lastSeenValue = summaryCard(summary, "마지막 수신");

    const queuePanel = element("section", "panel");
    const queueHead = element("div", "panel-head");
    queueHead.append(element("h2", "", "확인할 경보"), link("#/alerts", "more", "경보 목록 전체 →"));
    const queue = element("ol", "queue");
    const empty = element("p", "muted empty", "확인할 경보가 없습니다.");
    queuePanel.append(queueHead, queue, empty);

    // 오늘 시간 막대: 0시~24시 위에 경보를 점으로, 청소 시간대를 회색 띠로
    const dayPanel = element("section", "panel");
    const dayHead = element("div", "panel-head");
    const dayTotals = element("p", "muted small-text");
    dayHead.append(element("h2", "", "오늘"), dayTotals);
    const dayBar = element("div", "day-bar");
    const dayAxis = element("div", "day-axis");
    for (const hour of ["00", "06", "12", "18", "24"]) dayAxis.append(element("span", "", hour));
    dayPanel.append(
      dayHead,
      dayBar,
      dayAxis,
      element("p", "muted small", "꽉 찬 점은 미확인, 빈 점은 확인된 경보입니다. 회색 띠는 청소 시간대입니다. 점을 누르면 경보 상세로 갑니다."),
    );

    const planPanel = element("section", "panel");
    const planHead = element("div", "panel-head");
    planHead.append(element("h2", "", "평면도"), link("#/space", "more", "3D로 보기 →"));
    const svg = plan.createSvg("칸 평면도");
    planPanel.append(planHead, svg, element("p", "muted small", "위에서 본 모습 · 위쪽이 뒷벽, 아래쪽이 출입문"));

    const main = element("div", "stack");
    main.append(queuePanel, dayPanel);
    const layout = element("div", "layout");
    layout.append(main, planPanel);
    root.append(summary, layout);

    function drawDay() {
      const { state } = store;
      const today = [...state.alerts.values()].filter((a) => isToday(a.started_at));
      const unacked = today.filter((a) => a.status === "unacked").length;
      const cleaning = today.filter((a) => a.in_cleaning_window).length;
      dayTotals.textContent = state.loaded
        ? `경보 ${today.length} · 확인 ${today.length - unacked} · 미확인 ${unacked}` + (cleaning ? ` · 청소 시간대 ${cleaning}` : "")
        : "";

      dayBar.replaceChildren(element("div", "day-track"));
      for (const cleaningWindow of state.room ? state.room.cleaning_windows : []) {
        const band = element("div", "day-cleaning");
        const start = minutesOf(cleaningWindow.start);
        band.style.left = percent(start);
        band.style.width = percent(minutesOf(cleaningWindow.end) - start);
        band.title = `청소 ${cleaningWindow.start.slice(0, 5)}~${cleaningWindow.end.slice(0, 5)}`;
        dayBar.append(band);
      }
      // 미확인을 나중에 그려 겹칠 때 위에 오게 한다
      const ordered = [...today].sort((a, b) => (a.status === b.status ? 0 : a.status === "unacked" ? 1 : -1));
      for (const alert of ordered) {
        const label = `${GRADE[alert.grade].label} · ${where(alert, state.room)} · ${clock(alert.started_at)}`;
        const dot = link(`#/alerts/${alert.alert_id}`, `day-dot ${alert.grade} ${alert.status}`);
        dot.style.left = percent(minutesOfDate(new Date(alert.started_at)));
        dot.title = label;
        dot.setAttribute("aria-label", label);
        dayBar.append(dot);
      }
      const now = element("div", "day-now");
      now.style.left = percent(minutesOfDate(new Date()));
      now.title = "지금";
      dayBar.append(now);
    }

    function update(change) {
      const { state } = store;

      if (change.kind === "all" || change.kind === "devices") {
        const seen = [...state.devices.values()].map((d) => d.last_seen).filter(Boolean).sort();
        lastSeenValue.textContent = seen.length ? clock(seen.at(-1)) : "-";
      }
      if (change.kind === "tick") drawDay(); // "지금" 표시를 옮긴다
      if (change.kind === "devices" || change.kind === "connection" || change.kind === "tick") return;

      const unacked = store.unackedAlerts();
      if (state.loaded) {
        const today = [...state.alerts.values()].filter((a) => isToday(a.started_at));
        const cleaning = today.filter((a) => a.in_cleaning_window).length;
        unackedValue.textContent = unacked.length;
        todayCount.textContent = today.length;
        todaySub.textContent = cleaning ? `(청소 시간대 ${cleaning})` : "";
      }

      syncList(queue, unacked, {
        key: (a) => a.alert_id,
        sig: (a) => `${a.updated_at}|${state.acking.get(a.alert_id) || ""}`,
        build: queueItem,
      });
      empty.hidden = !state.loaded || unacked.length > 0;

      // 확인 처리 중 표시만 바뀐 경우에는 평면도와 시간 막대를 다시 그리지 않는다
      if (change.kind !== "ack") {
        if (state.room) plan.drawPlan(svg, state.room, plan.gradesByRoi(unacked));
        drawDay();
      }
    }

    update({ kind: "all" });
    return { update };
  }

  function summaryCard(parent, label) {
    const card = element("div", "card");
    const value = element("p", "value", "-");
    card.append(element("p", "label", label), value);
    parent.append(card);
    return value;
  }

  function queueItem(alert) {
    const { state } = store;
    const item = element("li", `queue-item ${alert.grade}`);
    const row = link(`#/alerts/${alert.alert_id}`, "queue-link");
    row.append(gradeBadge(alert.grade), element("span", "where", where(alert, state.room)));
    if (alert.in_cleaning_window) row.append(element("span", "tag", "청소 시간대"));
    row.append(timeLabel(alert.started_at));
    item.append(row, ackControls(alert, state.acking.get(alert.alert_id), store.ack));
    return item;
  }

  // "10:00:00" → 600
  function minutesOf(time) {
    const [hours, minutes] = time.split(":").map(Number);
    return hours * 60 + minutes;
  }

  function minutesOfDate(date) {
    return date.getHours() * 60 + date.getMinutes() + date.getSeconds() / 60;
  }

  function percent(minutes) {
    return `${((minutes / DAY_MINUTES) * 100).toFixed(3)}%`;
  }

  RaFi.views = RaFi.views || {};
  RaFi.views.home = { mount };
})((window.RaFi = window.RaFi || {}));
