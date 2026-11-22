// 칸 3D 보기 (공간 보기에서 쓰고, 나중에 사건 재생에서도 쓴다)
//
// 라이브러리 없이 직교 투영으로 그린다. 면마다 깊이를 구해 먼 것부터 칠한다(화가 알고리즘).
// 좌표는 설정 파일(config/rafi.yaml)의 cm 그대로: x 좌측 칸막이 → 우측, y 뒷벽 → 문, z 바닥 → 천장.
// "위에서" 시점은 2D 평면도와 같은 방향(위쪽이 뒷벽, x가 오른쪽)으로 보이게 맞췄다.

(function (RaFi) {
  "use strict";

  const { svgElement, GRADE } = RaFi.dom;

  const VIEW_W = 760;
  const VIEW_H = 560;
  const MAX_PITCH = Math.PI / 2 - 0.02; // 바로 위에서
  const MIN_PITCH = 0.05; // 거의 옆에서
  const STEP = Math.PI / 8;
  const MARGIN = 36; // 이름표가 잘리지 않을 여백 (px)

  // [yaw, pitch]: yaw는 수직축 회전, pitch는 내려다보는 각도
  const PRESETS = {
    oblique: [-0.65, 0.62],
    top: [0, MAX_PITCH],
    door: [0, 0.18],
    radar: [Math.PI, 0.75],
  };

  const KIND = { radar: "레이더", rf: "RF" };

  function create(container) {
    const svg = svgElement("svg", {
      class: "room3d",
      viewBox: `0 0 ${VIEW_W} ${VIEW_H}`,
      role: "img",
      "aria-label": "칸 3D 보기. 끌거나 방향키로 돌릴 수 있습니다",
      tabindex: "0",
    });
    container.append(svg);

    const view = { yaw: PRESETS.oblique[0], pitch: PRESETS.oblique[1] };
    let scene = null; // { room, grades: Map(roi → 등급), selected: roi | null, layers: { fixtures, zones } }
    let drag = null;

    svg.addEventListener("pointerdown", (event) => {
      drag = { x: event.clientX, y: event.clientY, yaw: view.yaw, pitch: view.pitch };
      svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener("pointermove", (event) => {
      if (!drag) return;
      view.yaw = drag.yaw + (event.clientX - drag.x) * 0.008;
      view.pitch = clamp(drag.pitch - (event.clientY - drag.y) * 0.006);
      draw();
    });
    const endDrag = () => {
      drag = null;
    };
    svg.addEventListener("pointerup", endDrag);
    svg.addEventListener("pointercancel", endDrag);

    // 그림에 초점이 있을 때만 방향키로 돌린다
    svg.addEventListener("keydown", (event) => {
      const moves = {
        ArrowLeft: [-STEP, 0],
        ArrowRight: [STEP, 0],
        ArrowUp: [0, 0.15],
        ArrowDown: [0, -0.15],
      };
      const move = moves[event.key];
      if (!move) return;
      event.preventDefault();
      view.yaw += move[0];
      view.pitch = clamp(view.pitch + move[1]);
      draw();
    });

    function draw() {
      if (!scene || !scene.room) return;
      const { room, grades, selected, layers } = scene;
      const size = room.size_cm;
      const project = projector(view, size);
      const faces = [];

      // 칸: 바닥, 뒷벽·양쪽 칸막이(반투명), 천장 테두리. 칸 전체(stall) 경보는 벽 색으로 알린다
      const stallGrade = grades.get("stall") || "";
      const roomFaces = boxFaces({ x_cm: [0, size.x], y_cm: [0, size.y], z_cm: [0, size.z] });
      faces.push(face(project, roomFaces[0], "floor"));
      faces.push(face(project, roomFaces[2], `wall ${stallGrade}`));
      faces.push(face(project, roomFaces[4], `wall side ${stallGrade}`));
      faces.push(face(project, roomFaces[5], `wall side ${stallGrade}`));
      faces.push(face(project, roomFaces[1], "ceiling"));

      if (layers.fixtures) {
        for (const fixture of Object.values(room.fixtures)) {
          for (const corners of boxFaces(fixture)) faces.push(face(project, corners, "fixture"));
        }
      }

      for (const [zoneId, zone] of Object.entries(room.zones)) {
        if (zoneId === "stall") continue; // 칸 전체는 상자 대신 벽 색으로
        const grade = grades.get(zoneId) || "";
        const isSelected = zoneId === selected;
        if (!grade && !isSelected && !layers.zones) continue;
        const className = `zone ${grade} ${isSelected ? "selected" : ""}`;
        for (const corners of boxFaces(zone)) faces.push(face(project, corners, className));
      }

      faces.sort((a, b) => b.depth - a.depth);
      const nodes = faces.map((f) => svgElement("polygon", { points: f.points, class: `face ${f.className}` }));

      // 장비와 이름표는 면 위에 그린다
      for (const [deviceId, device] of Object.entries(room.devices)) {
        if (!device.position_cm) continue;
        const { x, y, z } = device.position_cm;
        const point = project(x, y, z);
        nodes.push(svgElement("circle", { cx: point.sx, cy: point.sy, r: 6, class: "radar" }));
        nodes.push(label(project(x, y, z + 8), KIND[device.kind] || deviceId, "device-name"));
      }
      const namedZones = Object.entries(room.zones)
        .filter(([zoneId]) => zoneId !== "stall" && (zoneId === selected || grades.has(zoneId)))
        .map(([zoneId, zone]) => ({ zoneId, zone }));
      for (const { zoneId, zone } of namedZones) {
        // 이름 앞에 등급 글립을 붙인다. 3D 면은 보는 각도에 따라 색이 달라 보이므로
        // 색 외의 단서가 평면도보다 더 필요하다 (배지·평면도와 같은 기호를 쓴다)
        const grade = grades.get(zoneId) || "";
        const name = grade ? `${GRADE[grade].glyph} ${zone.name}` : zone.name;
        nodes.push(label(project(mid(zone.x_cm), mid(zone.y_cm), zone.z_cm[1]), name, `zone-name ${grade}`));
      }
      if (layers.fixtures) {
        for (const fixture of Object.values(room.fixtures)) {
          // 강조한 구역과 겹치는 기물은 이름을 숨겨 구역 이름이 읽히게 한다 (평면도와 같은 규칙)
          // 칸 폭 전체에 걸친 구역(높은 곳, 선반 아래)까지 따지면 기물 이름이 거의 다 사라지므로 작은 구역만 따진다
          const small = ({ zone }) => area(zone) < 0.4 * size.x * size.y;
          if (namedZones.filter(small).some(({ zone }) => overlapsXY(zone, fixture))) continue;
          nodes.push(label(project(mid(fixture.x_cm), mid(fixture.y_cm), fixture.z_cm[1]), fixture.name, "fixture-name"));
        }
      }
      svg.replaceChildren(...nodes);
    }

    return {
      setScene(next) {
        scene = next;
        draw();
      },
      setView(name) {
        [view.yaw, view.pitch] = PRESETS[name];
        draw();
      },
      rotate(direction) {
        view.yaw += direction * STEP;
        draw();
      },
    };
  }

  // 어느 각도에서 보든 칸이 그림을 꽉 채우도록 칸 모서리 8개가 들어가게 맞춘다
  function projector(view, size) {
    const cy = Math.cos(view.yaw);
    const sy = Math.sin(view.yaw);
    const cp = Math.cos(view.pitch);
    const sp = Math.sin(view.pitch);
    const raw = (x, y, z) => {
      const dx = x - size.x / 2;
      const dy = y - size.y / 2;
      const dz = z - size.z / 2;
      const xr = dx * cy - dy * sy;
      const yr = dx * sy + dy * cy;
      return { u: xr, v: -dz * cp + yr * sp, depth: -(yr * cp + dz * sp) }; // depth: 클수록 멀다
    };

    const corners = [];
    for (const x of [0, size.x]) for (const y of [0, size.y]) for (const z of [0, size.z]) corners.push(raw(x, y, z));
    const us = corners.map((c) => c.u);
    const vs = corners.map((c) => c.v);
    const [u0, u1, v0, v1] = [Math.min(...us), Math.max(...us), Math.min(...vs), Math.max(...vs)];
    const scale = Math.min((VIEW_W - MARGIN * 2) / (u1 - u0), (VIEW_H - MARGIN * 2) / (v1 - v0));
    const offsetX = VIEW_W / 2 - ((u0 + u1) / 2) * scale;
    const offsetY = VIEW_H / 2 - ((v0 + v1) / 2) * scale;

    return (x, y, z) => {
      const p = raw(x, y, z);
      return { sx: offsetX + p.u * scale, sy: offsetY + p.v * scale, depth: p.depth };
    };
  }

  // 두께가 0인 축이 있으면(선반, 문, 환풍구 등) 면 하나만 돌려준다
  function boxFaces(area) {
    const [x0, x1] = area.x_cm;
    const [y0, y1] = area.y_cm;
    const [z0, z1] = area.z_cm;
    if (z0 === z1) return [[[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]]];
    if (y0 === y1) return [[[x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]]];
    if (x0 === x1) return [[[x0, y0, z0], [x0, y1, z0], [x0, y1, z1], [x0, y0, z1]]];
    return [
      [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]], // 바닥 쪽
      [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], // 위쪽
      [[x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]], // 뒷벽 쪽
      [[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]], // 문 쪽
      [[x0, y0, z0], [x0, y1, z0], [x0, y1, z1], [x0, y0, z1]], // 좌측 칸막이 쪽
      [[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]], // 우측 칸막이 쪽
    ];
  }

  function face(project, corners, className) {
    let depth = 0;
    const points = corners.map(([x, y, z]) => {
      const p = project(x, y, z);
      depth += p.depth;
      return `${p.sx.toFixed(1)},${p.sy.toFixed(1)}`;
    });
    return { points: points.join(" "), depth: depth / corners.length, className };
  }

  function label(point, content, className) {
    const node = svgElement("text", { x: point.sx.toFixed(1), y: (point.sy - 6).toFixed(1), class: className });
    node.textContent = content;
    return node;
  }

  function area(zone) {
    return (zone.x_cm[1] - zone.x_cm[0]) * (zone.y_cm[1] - zone.y_cm[0]);
  }

  function overlapsXY(a, b) {
    return (
      a.x_cm[0] < b.x_cm[1] && b.x_cm[0] < a.x_cm[1] &&
      a.y_cm[0] < b.y_cm[1] && b.y_cm[0] < a.y_cm[1]
    );
  }

  function mid([a, b]) {
    return (a + b) / 2;
  }

  function clamp(pitch) {
    return Math.max(MIN_PITCH, Math.min(MAX_PITCH, pitch));
  }

  RaFi.room3d = { create };
})((window.RaFi = window.RaFi || {}));
