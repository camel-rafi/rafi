// 공간 보기: 칸을 3D로 돌려 보며 경보 구역·기물·레이더 위치를 확인한다
//
// #/space          구역을 고르지 않은 상태
// #/space/<roi>    그 구역을 고른 상태 (경보 상세의 "3D로 보기"에서 들어옴)

(function (RaFi) {
  "use strict";

  const { element, link, syncList, GRADE } = RaFi.dom;
  const { store, plan, room3d } = RaFi;

  const VIEWS = [
    ["oblique", "비스듬히"],
    ["top", "위에서"],
    ["door", "문에서"],
    ["radar", "레이더 쪽에서"],
  ];

  function mount(root, [initialRoi]) {
    let selected = initialRoi || null;
    const layers = { fixtures: true, zones: true };

    const crumbs = element("p", "crumbs");
    crumbs.append(link("#/", "", "개요"), " › 공간 보기");
    const head = element("div", "page-head");
    const title = element("h1", "", "공간 보기");
    const size = element("p", "muted");
    head.append(title, size);

    // ---------- 3D ----------
    const stage = element("section", "panel stage");
    const bar = element("div", "stage-bar");
    bar.append(element("span", "hint", "끌거나 방향키로 돌리기"));
    for (const [name, text] of VIEWS) {
      const button = element("button", "", text);
      button.type = "button";
      button.addEventListener("click", () => viewer.setView(name));
      bar.append(button);
    }
    for (const [direction, text, labelText] of [[-1, "‹", "왼쪽으로 돌리기"], [1, "›", "오른쪽으로 돌리기"]]) {
      const button = element("button", "icon", text);
      button.type = "button";
      button.setAttribute("aria-label", labelText);
      button.addEventListener("click", () => viewer.rotate(direction));
      bar.append(button);
    }
    stage.append(bar);
    const viewer = room3d.create(stage);
    stage.append(element("p", "muted small stage-note", "'위에서'는 평면도와 같은 방향입니다 (위쪽이 뒷벽). 색이 칠해진 구역은 미확인 경보가 있는 곳입니다."));

    // ---------- 구역 목록·표시 설정 ----------
    const side = element("section", "panel");
    const zoneList = element("ol", "zone-list");
    side.append(element("h2", "", "경보 구역"), element("p", "muted small", "누르면 3D에서 강조합니다. 높이는 바닥 기준입니다."), zoneList);

    const options = element("fieldset", "layers");
    options.append(element("legend", "", "표시"));
    for (const [key, text] of [["fixtures", "기물 (변기·선반·디스펜서 등)"], ["zones", "경보 없는 구역도 흐리게"]]) {
      const labelNode = element("label", "check");
      const input = element("input");
      input.type = "checkbox";
      input.checked = layers[key];
      input.addEventListener("change", () => {
        layers[key] = input.checked;
        render();
      });
      labelNode.append(input, text);
      options.append(labelNode);
    }
    side.append(options);

    const layout = element("div", "layout space");
    layout.append(stage, side);
    root.append(crumbs, head, layout);

    function render() {
      const { state } = store;
      if (!state.room) {
        size.textContent = "불러오는 중…";
        return;
      }
      const { x, y, z } = state.room.size_cm;
      title.textContent = `공간 보기 · ${state.room.name}`;
      size.textContent = `${x} × ${y} × ${z} cm`;

      const unacked = store.unackedAlerts();
      const grades = plan.gradesByRoi(unacked);
      const counts = new Map();
      for (const alert of unacked) if (alert.roi) counts.set(alert.roi, (counts.get(alert.roi) || 0) + 1);

      const zones = Object.entries(state.room.zones)
        .filter(([zoneId]) => zoneId !== "stall")
        .map(([zoneId, zone]) => ({ id: zoneId, ...zone }));
      syncList(zoneList, zones, {
        key: (zone) => zone.id,
        sig: (zone) => `${zone.id === selected}|${counts.get(zone.id) || 0}|${grades.get(zone.id) || ""}`,
        build: (zone) => zoneItem(zone, zone.id === selected, counts.get(zone.id) || 0, grades.get(zone.id)),
      });

      viewer.setScene({ room: state.room, grades, selected, layers });
    }

    function zoneItem(zone, isSelected, count, grade) {
      const item = element("li");
      const button = element("button", `zone-button ${isSelected ? "selected" : ""}`);
      button.type = "button";
      button.setAttribute("aria-pressed", String(isSelected));
      const name = element("span", "name", zone.name);
      const meta = element("span", "meta", `높이 ${zone.z_cm[0]}–${zone.z_cm[1]} cm`);
      button.append(name, meta);
      if (count) button.append(element("span", `badge ${grade}`, `${GRADE[grade].label.slice(0, 2)} ${count}건`));
      button.addEventListener("click", () => {
        selected = isSelected ? null : zone.id;
        history.replaceState(null, "", selected ? `#/space/${selected}` : "#/space");
        render();
      });
      item.append(button);
      return item;
    }

    function update(change) {
      if (change.kind === "devices" || change.kind === "connection" || change.kind === "ack") return;
      render();
    }

    render();
    return { update };
  }

  RaFi.views = RaFi.views || {};
  RaFi.views.space = { mount };
})((window.RaFi = window.RaFi || {}));
