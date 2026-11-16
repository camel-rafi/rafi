// 칸 평면도 (위에서 본 모습)
//
// 좌표는 cm 그대로 쓴다. y는 뒷벽(위)에서 문(아래)으로 커지므로 SVG 방향과 같다.

(function (RaFi) {
  "use strict";

  const { svgElement, GRADE, highestGrade } = RaFi.dom;

  // 위에서 보면 칸 대부분을 덮는 구역. 구역으로 그리지 않고, 경보가 있으면 칸 테두리로 강조한다
  const WIDE_ZONES = new Set(["high", "stall"]);
  const KIND = { radar: "레이더", rf: "RF" };

  // 경보 목록 → 구역별 가장 높은 등급
  function gradesByRoi(alerts) {
    const grades = new Map();
    for (const alert of alerts) {
      if (!alert.roi) continue;
      grades.set(alert.roi, highestGrade([grades.get(alert.roi), alert.grade]));
    }
    return grades;
  }

  function drawPlan(svg, room, grades) {
    const margin = 12;
    const { x: width, y: depth } = room.size_cm;
    svg.setAttribute("viewBox", `${-margin} ${-margin} ${width + margin * 2} ${depth + margin * 2}`);
    svg.replaceChildren();

    const wide = [...grades.entries()].filter(([roi]) => WIDE_ZONES.has(roi));
    const wideGrade = highestGrade(wide.map(([, grade]) => grade));

    svg.append(rect(0, 0, width, depth, `room ${wideGrade || ""}`));

    const activeZones = [...grades.keys()]
      .filter((roi) => !WIDE_ZONES.has(roi) && room.zones[roi])
      .map((roi) => room.zones[roi]);
    for (const fixture of Object.values(room.fixtures)) {
      // 경보가 뜬 구역과 겹치는 기물은 이름을 숨겨 구역 이름이 읽히게 한다
      const covered = activeZones.some((zone) => overlaps(zone, fixture));
      drawArea(svg, fixture, "fixture", covered ? null : fixture.name);
    }
    for (const [zoneId, zone] of Object.entries(room.zones)) {
      if (WIDE_ZONES.has(zoneId)) continue;
      const grade = grades.get(zoneId);
      drawArea(svg, zone, `zone ${grade || ""}`, grade ? zone.name : null, "zone-label");
    }

    for (const [deviceId, device] of Object.entries(room.devices)) {
      if (!device.position_cm) continue;
      const { x, y } = device.position_cm;
      svg.append(svgElement("circle", { cx: x, cy: y, r: 2.2, class: "radar" }));
      svg.append(text(x, y - 3.5, KIND[device.kind] || deviceId));
    }

    // 위치가 넓은 구역(높은 곳, 칸 전체)의 경보는 칸 위에 글자로 알린다
    wide.forEach(([roi, grade], index) => {
      const label = text(width / 2, -4 - index * 6, `${room.zones[roi].name} · ${GRADE[grade].label}`);
      label.setAttribute("class", `room-alert ${grade}`);
      svg.append(label);
    });

    svg.append(text(width / 2, depth + 8, "출입문"));
  }

  function drawArea(svg, area, className, label, labelClass) {
    const [x0, x1] = area.x_cm;
    const [y0, y1] = area.y_cm;
    if (y0 === y1 || x0 === x1) {
      // 두께를 모르는 면(문 등)은 선으로 그린다
      svg.append(svgElement("line", { x1: x0, y1: y0, x2: x1, y2: y1, class: "fixture-line" }));
      return;
    }
    svg.append(rect(x0, y0, x1 - x0, y1 - y0, className));
    if (label) {
      const node = text((x0 + x1) / 2, (y0 + y1) / 2 + 1.5, label);
      if (labelClass) node.setAttribute("class", labelClass);
      svg.append(node);
    }
  }

  function overlaps(a, b) {
    return (
      a.x_cm[0] < b.x_cm[1] && b.x_cm[0] < a.x_cm[1] &&
      a.y_cm[0] < b.y_cm[1] && b.y_cm[0] < a.y_cm[1]
    );
  }

  function rect(x, y, width, height, className) {
    return svgElement("rect", { x, y, width, height, rx: 1, class: className });
  }

  function text(x, y, content) {
    const node = svgElement("text", { x, y, "text-anchor": "middle" });
    node.textContent = content;
    return node;
  }

  function createSvg(label) {
    return svgElement("svg", { class: "plan", role: "img", "aria-label": label });
  }

  RaFi.plan = { gradesByRoi, drawPlan, createSvg };
})((window.RaFi = window.RaFi || {}));
