// 2 · 경보 상세: 경보 하나의 근거와 위치를 보고 확인 처리한다

(function (RaFi) {
  "use strict";

  const { element, link, dateTime, where, gradeBadge, evidenceList, ackControls } = RaFi.dom;
  const { store, plan } = RaFi;

  function mount(root, [idText]) {
    const alertId = Number(idText);
    let lastSig = null;
    // 2초마다 다시 그려도 "이전 갱신"을 펼친 상태가 유지되도록 화면이 기억한다
    const view = { historyOpen: false };

    function update(change) {
      if (change.kind === "devices" || change.kind === "connection") return;
      if ((change.kind === "alert" || change.kind === "ack") && change.id !== alertId) return;

      const { state } = store;
      const alert = state.alerts.get(alertId);
      const sig = alert
        ? `${alert.updated_at}|${alert.status}|${state.acking.get(alertId) || ""}|${state.room ? 1 : 0}`
        : `missing|${state.loaded}`;
      if (sig === lastSig) return; // 다른 경보의 갱신이나 같은 내용이면 다시 그리지 않는다
      lastSig = sig;

      // toggle 이벤트는 펼친 직후가 아니라 조금 뒤에 오므로, 그 사이에 갱신이 와도 펼침을 잃지 않게 화면에서 직접 읽는다
      const shown = root.querySelector(".evidence-history");
      if (shown) view.historyOpen = shown.open;

      root.replaceChildren(...(alert ? render(alert, view) : missing(alertId, state.loaded)));
    }

    update({ kind: "all" });
    return { update };
  }

  function render(alert, view) {
    const { state } = store;

    const crumbs = element("p", "crumbs");
    crumbs.append(link("#/", "", "개요"), " › ", link("#/alerts", "", "경보 목록"), ` › 경보 #${alert.alert_id}`);

    const head = element("section", `panel detail-head ${alert.grade}`);
    const tags = element("div", "head");
    tags.append(
      gradeBadge(alert.grade),
      element("span", `status ${alert.status}`, alert.status === "unacked" ? "미확인" : "확인됨"),
    );
    if (alert.in_cleaning_window) tags.append(element("span", "tag", "청소 시간대"));

    const meta = [`경보 #${alert.alert_id}`, `${dateTime(alert.started_at)} 시작`, `마지막 갱신 ${dateTime(alert.updated_at)}`];
    head.append(
      tags,
      element("h1", "", where(alert, state.room)),
      element("p", "muted", meta.join(" · ")),
      ackControls(alert, state.acking.get(alert.alert_id), store.ack),
    );

    const evidencePanel = element("section", "panel");
    evidencePanel.append(
      element("h2", "", "판단 근거"),
      evidenceList(alert, {
        history: true,
        historyOpen: view.historyOpen,
        onToggle: (open) => {
          view.historyOpen = open;
        },
      }),
    );

    const planPanel = element("section", "panel");
    const planHead = element("div", "panel-head");
    planHead.append(element("h2", "", "위치"));
    if (alert.roi) planHead.append(link(`#/space/${alert.roi}`, "more", "3D로 보기 →"));
    planPanel.append(planHead);
    if (state.room) {
      const svg = plan.createSvg("경보 위치 평면도");
      plan.drawPlan(svg, state.room, alert.roi ? new Map([[alert.roi, alert.grade]]) : new Map());
      planPanel.append(svg);
      if (!alert.roi) planPanel.append(element("p", "muted small", "RF만으로 판단한 경보라 위치를 모릅니다."));
    }

    const layout = element("div", "layout");
    layout.append(evidencePanel, planPanel);
    return [crumbs, head, layout];
  }

  function missing(alertId, loaded) {
    if (!loaded) return [element("p", "muted empty", "불러오는 중…")];
    return [
      element("p", "muted empty", `경보 #${alertId}을(를) 찾을 수 없습니다. 최근 500건 밖이거나 없는 번호입니다.`),
      link("#/alerts", "", "경보 목록으로 가기"),
    ];
  }

  RaFi.views = RaFi.views || {};
  RaFi.views.detail = { mount };
})((window.RaFi = window.RaFi || {}));
