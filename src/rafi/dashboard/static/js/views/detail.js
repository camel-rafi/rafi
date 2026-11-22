// 2 · 경보 상세: 경보 하나의 근거·시간 흐름·위치를 보고 확인 처리한다

(function (RaFi) {
  "use strict";

  const { element, link, clock, dateTime, where, gradeBadge, evidenceList, groupEvidence, eventSpan, latestRadarGroup, ongoingTag, ackControls } = RaFi.dom;
  const { store, plan } = RaFi;

  function mount(root, [idText]) {
    const alertId = Number(idText);
    let lastSig = null;
    // 2초마다 다시 그려도 "이전 갱신"을 펼친 상태가 사건별로 유지되도록 화면이 기억한다
    const view = { openHistory: new Set() };

    function update(change) {
      if (change.kind === "devices" || change.kind === "connection" || change.kind === "tick") return;
      if ((change.kind === "alert" || change.kind === "ack") && change.id !== alertId) return;

      const { state } = store;
      const alert = state.alerts.get(alertId);
      const sig = alert
        ? `${alert.updated_at}|${alert.status}|${state.acking.get(alertId) || ""}|${state.room ? 1 : 0}|${store.isOngoing(alertId)}`
        : `missing|${state.loaded}`;
      if (sig === lastSig) return; // 다른 경보의 갱신이나 같은 내용이면 다시 그리지 않는다
      lastSig = sig;

      // 펼침 상태는 다시 그리기 직전에 화면에서 직접 읽는다 (toggle 이벤트는 조금 늦게 오므로)
      for (const details of root.querySelectorAll(".evidence-history")) {
        if (details.open) view.openHistory.add(details.dataset.key);
        else view.openHistory.delete(details.dataset.key);
      }

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
    if (store.isOngoing(alert.alert_id)) tags.append(ongoingTag(alert));
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
      evidenceList(alert, { history: true, openHistory: view.openHistory }),
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
    return [crumbs, head, timeline(alert, store.isOngoing(alert.alert_id)), layout];
  }

  // 시간 흐름: 판단 근거의 사건 묶음마다 한 줄 (레이더 사건, RF 판단).
  // 축은 첫 사건 시작부터 마지막 끝까지. 레이더 막대 위 눈금은 2초 갱신이 온 시각(그 갱신의 event_end)이다.
  // 등급이 바뀐 시점(L1 → L2)은 서버가 이력을 저장하지 않아 그리지 않는다.
  const MIN_AXIS_MS = 10000; // 아주 짧은 사건도 막대가 보이도록

  // live: 진행 중이면 가장 최근 레이더 사건 막대 끝을 깜빡인다
  function timeline(alert, live) {
    const groups = groupEvidence(alert.evidence);
    const current = live ? latestRadarGroup(alert) : null;
    const panel = element("section", "panel timeline");
    panel.append(element("h2", "", "시간 흐름"));

    const t0 = Math.min(...groups.map((g) => ms(g.start)));
    const t1 = Math.max(t0 + MIN_AXIS_MS, ...groups.map((g) => ms(g.end)));
    const position = (t) => `${(((t - t0) / (t1 - t0)) * 100).toFixed(3)}%`;

    const rows = element("div", "timeline-rows");
    for (const group of groups) {
      const span = eventSpan(group.start, group.end);
      const track = element("div", "timeline-track");
      track.setAttribute("role", "img");
      track.setAttribute("aria-label", `${group.label} ${span}`);
      const isCurrent = current && current.key === group.key;
      const bar = element("div", `timeline-bar ${group.source}${isCurrent ? " live" : ""}`);
      bar.style.left = position(ms(group.start));
      bar.style.width = `calc(${position(ms(group.end))} - ${position(ms(group.start))})`;
      bar.title = `${group.label} · ${span}${isCurrent ? " · 진행 중" : ""}`;
      track.append(bar);
      if (group.source === "radar") {
        for (const update of [group.latest, ...group.history]) {
          const tick = element("span", "timeline-tick");
          tick.style.left = position(ms(update.event_end));
          tick.title = `갱신 ${clock(update.event_end)}`;
          track.append(tick);
        }
      }
      rows.append(element("span", "timeline-label", group.label), track);
    }

    const axis = element("div", "timeline-axis");
    for (let i = 0; i <= 4; i += 1) {
      axis.append(element("span", "", clock(new Date(t0 + ((t1 - t0) * i) / 4).toISOString())));
    }
    const axisRow = element("div", "timeline-rows");
    axisRow.append(element("span"), axis);

    panel.append(
      rows,
      axisRow,
      element("p", "muted small", "주황은 레이더 사건(눈금은 2초 갱신이 온 시각), 파랑은 RF 판단입니다. 막대에 마우스를 올리면 구간이 보입니다."),
    );
    return panel;
  }

  function ms(iso) {
    return new Date(iso).getTime();
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
