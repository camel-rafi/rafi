// 1 · 경보 목록: 미확인·확인된 경보를 모두 보고 확인 처리한다

(function (RaFi) {
  "use strict";

  const { element, link, clock, where, gradeBadge, evidenceList, ackControls, syncList } = RaFi.dom;
  const { store } = RaFi;

  const FILTERS = [
    { id: "all", label: "전체" },
    { id: "unacked", label: "미확인" },
    { id: "acked", label: "확인됨" },
  ];

  function mount(root) {
    let filter = "all";

    const crumbs = element("p", "crumbs");
    crumbs.append(link("#/", "", "개요"), " › 경보 목록");

    const head = element("div", "page-head");
    const group = element("div", "segmented");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "목록 필터");
    const buttons = FILTERS.map((f) => {
      const button = element("button", "", f.label);
      button.type = "button";
      button.addEventListener("click", () => {
        filter = f.id;
        render();
      });
      group.append(button);
      return button;
    });
    head.append(element("h1", "", "경보 목록"), group);

    const list = element("ol", "alerts");
    const empty = element("p", "muted empty");
    const panel = element("section", "panel");
    panel.append(list, empty);
    root.append(crumbs, head, panel);

    function render() {
      const { state } = store;
      const unacked = store.unackedAlerts();
      const acked = store.ackedAlerts();
      const counts = { all: unacked.length + acked.length, unacked: unacked.length, acked: acked.length };
      FILTERS.forEach((f, index) => {
        buttons[index].textContent = state.loaded ? `${f.label} ${counts[f.id]}` : f.label;
        buttons[index].setAttribute("aria-pressed", String(f.id === filter));
      });

      const items = filter === "unacked" ? unacked : filter === "acked" ? acked : [...unacked, ...acked];
      syncList(list, items, {
        key: (a) => a.alert_id,
        sig: (a) => `${a.updated_at}|${a.status}|${state.acking.get(a.alert_id) || ""}`,
        build: alertItem,
      });
      empty.hidden = !state.loaded || items.length > 0;
      empty.textContent = filter === "unacked" ? "확인할 경보가 없습니다." : "경보가 없습니다.";
    }

    function update(change) {
      if (change.kind === "devices" || change.kind === "connection") return;
      render();
    }

    render();
    return { update };
  }

  function alertItem(alert) {
    const { state } = store;
    const item = element("li", `alert ${alert.status}`);

    const head = element("div", "head");
    head.append(gradeBadge(alert.grade), link(`#/alerts/${alert.alert_id}`, "where", where(alert, state.room)));
    if (alert.in_cleaning_window) head.append(element("span", "tag", "청소 시간대"));
    head.append(element("span", "time", clock(alert.started_at)));

    item.append(head, evidenceList(alert), ackControls(alert, state.acking.get(alert.alert_id), store.ack));
    return item;
  }

  RaFi.views = RaFi.views || {};
  RaFi.views.alerts = { mount };
})((window.RaFi = window.RaFi || {}));
