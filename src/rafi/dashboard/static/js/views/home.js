// 0 · 개요: 지금 칸 상태와 확인할 경보

(function (RaFi) {
  "use strict";

  const { element, link, clock, isToday, where, gradeBadge, ackControls, syncList } = RaFi.dom;
  const { store, plan } = RaFi;

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

    const planPanel = element("section", "panel");
    const svg = plan.createSvg("칸 평면도");
    const planHead = element("div", "panel-head");
    planHead.append(element("h2", "", "평면도"), link("#/space", "more", "3D로 보기 →"));
    planPanel.append(
      planHead,
      svg,
      element("p", "muted small", "위에서 본 모습 · 위쪽이 뒷벽, 아래쪽이 출입문"),
    );

    const layout = element("div", "layout");
    layout.append(queuePanel, planPanel);
    root.append(summary, layout);

    function update(change) {
      const { state } = store;
      const unacked = store.unackedAlerts();

      if (change.kind === "all" || change.kind === "devices") {
        const seen = [...state.devices.values()].map((d) => d.last_seen).filter(Boolean).sort();
        lastSeenValue.textContent = seen.length ? clock(seen.at(-1)) : "-";
      }
      if (change.kind === "devices" || change.kind === "connection") return;

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

      // 확인 처리 중 표시만 바뀐 경우에는 평면도를 다시 그리지 않는다
      if (change.kind !== "ack" && state.room) {
        plan.drawPlan(svg, state.room, plan.gradesByRoi(unacked));
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
    row.append(element("span", "time", clock(alert.started_at)));
    item.append(row, ackControls(alert, state.acking.get(alert.alert_id), store.ack));
    return item;
  }

  RaFi.views = RaFi.views || {};
  RaFi.views.home = { mount };
})((window.RaFi = window.RaFi || {}));
