// 칸 평면도 (위에서 본 모습)
//
// 좌표는 cm 그대로 쓴다. y는 뒷벽(위)에서 문(아래)으로 커지므로 SVG 방향과 같다.

(function (RaFi) {
  "use strict";

  const { element, svgElement, gradeText, highestGrade, GRADE } = RaFi.dom;

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
    svg.replaceChildren(defs()); // 무늬 정의는 다시 그릴 때마다 함께 넣는다

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
    const labels = [];
    for (const [zoneId, zone] of Object.entries(room.zones)) {
      if (WIDE_ZONES.has(zoneId)) continue;
      const grade = grades.get(zoneId);
      // 이름 앞에 등급 글립을 붙인다 (배지와 같은 기호를 쓴다)
      const label = grade ? `${GRADE[grade].glyph} ${zone.name}` : null;
      const node = drawArea(svg, zone, `zone ${grade || ""}`, label, "zone-label");
      if (node) labels.push({ node, zone });
    }

    for (const [deviceId, device] of Object.entries(room.devices)) {
      if (!device.position_cm) continue;
      const { x, y } = device.position_cm;
      svg.append(svgElement("circle", { cx: x, cy: y, r: 2.2, class: "radar" }));
      svg.append(text(x, y - 3.5, KIND[device.kind] || deviceId));
    }

    // 위치가 넓은 구역(높은 곳, 칸 전체)의 경보는 칸 위에 글자로 알린다
    wide.forEach(([roi, grade], index) => {
      const label = text(width / 2, -4 - index * 6, `${room.zones[roi].name} · ${gradeText(grade)}`);
      label.setAttribute("class", `room-alert ${grade}`);
      svg.append(label);
    });

    svg.append(text(width / 2, depth + 8, "출입문"));
    clampLabels(labels, width, margin);
    describe(svg, room, grades);
  }

  // SVG 안의 글자는 role="img" 때문에 따로 읽히지 않는다.
  // 그래서 지금 어느 구역이 어떤 등급인지를 aria-label에 담는다 (무늬·색을 못 보는 경우의 통로)
  function describe(svg, room, grades) {
    const base = svg.dataset.label || "";
    const parts = [...grades.entries()].map(([roi, grade]) => {
      const zone = room.zones[roi];
      return `${zone ? zone.name : roi} ${gradeText(grade)}`;
    });
    svg.setAttribute("aria-label", parts.length ? `${base}. ${parts.join(", ")}` : `${base}. 경보 없음`);
  }

  function drawArea(svg, area, className, label, labelClass) {
    const [x0, x1] = area.x_cm;
    const [y0, y1] = area.y_cm;
    if (y0 === y1 || x0 === x1) {
      // 두께를 모르는 면(문 등)은 선으로 그린다
      svg.append(svgElement("line", { x1: x0, y1: y0, x2: x1, y2: y1, class: "fixture-line" }));
      return null;
    }
    svg.append(rect(x0, y0, x1 - x0, y1 - y0, className));
    if (!label) return null;
    const node = text((x0 + x1) / 2, (y0 + y1) / 2 + 1.5, label);
    if (labelClass) node.setAttribute("class", labelClass);
    svg.append(node);
    return node;
  }

  // 구역 이름은 구역보다 넓을 때가 많다 (좁은 구역은 12cm인데 이름은 20cm를 넘는다).
  // 그대로 두면 이름이 구역 무늬를 덮어 무늬가 안 보인다.
  // 그래서 이름이 구역보다 넓으면 구역 바로 위 빈 바닥으로 올리고,
  // 어느 쪽이든 칸 밖으로 나가지 않게 가로 위치를 가둔다.
  // getBBox()는 화면에 붙어 있어야 값이 나오므로 그리기가 끝난 뒤에 부른다.
  function clampLabels(labels, width, margin) {
    for (const { node, zone } of labels) {
      let box;
      try {
        box = node.getBBox();
      } catch {
        continue; // 화면에 없으면 다음에 다시 그릴 때 맞춘다
      }
      if (!box.width) continue;

      if (box.width > zone.x_cm[1] - zone.x_cm[0] - 1) {
        // 구역 위쪽으로 뺀다. 칸 위 여백보다 올라가지 않게 막는다
        node.setAttribute("y", Math.max(zone.y_cm[0] - 1.5, -margin + box.height).toFixed(2));
      }

      const half = box.width / 2;
      const x = Number(node.getAttribute("x"));
      const clamped = Math.min(Math.max(x, -margin + half), width + margin - half);
      if (clamped !== x) node.setAttribute("x", clamped.toFixed(2));
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

  // 등급별 채움 무늬.
  // 색만으로 등급을 알리면 흑백 인쇄나 적록색약에서 구분이 사라진다.
  // 무늬(여기) + 테두리 선종(style.css) + 글립이 붙은 이름(아래) 세 가지로 알린다.
  // 무늬 색은 CSS 클래스로 두어 테마가 바뀌면 같이 바뀐다.
  // 타일 크기는 cm 단위다 (viewBox가 칸 치수를 그대로 쓴다).
  // 가장 좁은 구역이 12cm 남짓이므로 타일이 2.5cm는 되어야 무늬로 읽힌다.
  const PATTERNS = {
    // 지금 확인: 교차 해치 (가장 촘촘하다)
    L2: { size: 2.5, paths: ["M0 0 L2.5 2.5", "M2.5 0 L0 2.5"] },
    // 확인 필요: 한 방향 사선
    L1: { size: 3, paths: ["M0 0 L3 3"] },
    // 참고: 드문 점 (RF만으로 판단해 위치를 모르는 경우라 평면도에는 거의 안 나온다)
    L3: { size: 3, paths: ["M1.5 1.5 L1.6 1.5"] },
  };

  function defs() {
    const node = svgElement("defs");
    for (const [grade, { size, paths }] of Object.entries(PATTERNS)) {
      const pattern = svgElement("pattern", {
        id: `rafi-plan-${grade}`,
        width: size,
        height: size,
        patternUnits: "userSpaceOnUse",
      });
      pattern.append(svgElement("rect", { width: size, height: size, class: `hatch-bg ${grade}` }));
      for (const d of paths) pattern.append(svgElement("path", { d, class: `hatch-line ${grade}` }));
      node.append(pattern);
    }
    return node;
  }

  function createSvg(label) {
    const svg = svgElement("svg", { class: "plan", role: "img", "aria-label": label });
    svg.dataset.label = label; // drawPlan이 뒤에 현재 상태를 붙일 때 쓴다
    svg.append(defs());
    return svg;
  }

  // 무늬가 무엇을 뜻하는지 평면도 아래에 적는다.
  // 범례가 없으면 무늬는 장식으로 읽힌다.
  //
  // 조각 SVG는 무늬를 다시 정의하지 않는다. 같은 id를 여러 번 쓰면 무효 HTML이고
  // 브라우저는 문서의 첫 번째 것만 쓴다. 대신 같은 문서에 있는 평면도의 defs를 참조한다
  // (url(#...)은 문서 전체에서 찾으므로 다른 <svg>에 있어도 된다).
  // 그래서 범례는 평면도와 같은 화면에만 붙인다.
  function swatch(grade) {
    const node = svgElement("svg", { class: "plan legend-swatch", viewBox: "0 0 24 14", "aria-hidden": "true" });
    node.append(svgElement("rect", { x: 1, y: 1, width: 22, height: 12, class: `zone ${grade}` }));
    return node;
  }

  function legend() {
    const box = element("div", "plan-legend");
    for (const grade of Object.keys(GRADE)) {
      const row = element("span", "plan-legend-item");
      row.append(swatch(grade), element("span", "", `${GRADE[grade].glyph} ${GRADE[grade].action}`));
      box.append(row);
    }
    const idle = element("span", "plan-legend-item");
    idle.append(swatch(""), element("span", "", "조용함"));
    box.append(idle);
    return box;
  }

  RaFi.plan = { gradesByRoi, drawPlan, createSvg, legend };
})((window.RaFi = window.RaFi || {}));
