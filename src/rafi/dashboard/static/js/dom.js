// 화면 공통 도우미
//
// 모듈이 보낸 문자열(근거 등)은 textContent로만 넣어 HTML로 해석되지 않게 한다.

(function (RaFi) {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";

  const GRADE = {
    L2: { label: "L2 우선 확인", order: 0 },
    L1: { label: "L1 확인 요청", order: 1 },
    L3: { label: "L3 알림", order: 2 },
  };
  const SOURCE = { radar: "레이더", rf: "RF" };

  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function svgElement(tag, attributes = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    return node;
  }

  function link(href, className, content) {
    const node = element("a", className, content);
    node.href = href;
    return node;
  }

  // ---------- 시각 ----------

  function clock(iso) {
    const time = new Date(iso);
    return [time.getHours(), time.getMinutes(), time.getSeconds()]
      .map((n) => String(n).padStart(2, "0"))
      .join(":");
  }

  function isToday(iso) {
    return new Date(iso).toDateString() === new Date().toDateString();
  }

  // 오늘이 아니면 날짜를 붙인다
  function dateTime(iso) {
    if (isToday(iso)) return clock(iso);
    const time = new Date(iso);
    return `${time.getMonth() + 1}/${time.getDate()} ${clock(iso)}`;
  }

  function hhmm(time) {
    return time.slice(0, 5);
  }

  // ---------- 경보 ----------

  function where(alert, room) {
    if (!alert.roi) return `${room ? room.name : alert.room} · 위치 미상`;
    const zone = room && room.zones[alert.roi];
    return zone ? zone.name : alert.roi;
  }

  function highestGrade(grades) {
    const valid = grades.filter(Boolean).sort((a, b) => GRADE[a].order - GRADE[b].order);
    return valid[0] || null;
  }

  function byPriority(a, b) {
    return GRADE[a.grade].order - GRADE[b.grade].order || b.started_at.localeCompare(a.started_at);
  }

  function byRecentUpdate(a, b) {
    return b.updated_at.localeCompare(a.updated_at);
  }

  function gradeBadge(grade) {
    return element("span", `badge ${grade}`, GRADE[grade].label);
  }

  // 근거 묶기
  // 레이더는 사건이 이어지는 동안 2초마다 갱신을 보내므로 같은 사건의 근거가 계속 쌓인다.
  // 레이더 근거는 하나로 묶어 가장 최근 것만 보이고 갱신 횟수를 붙인다.
  // (서버가 event_id를 처리하면 한 경보에 붙은 레이더 사건을 사건별로 나눌 수 있다)
  // RF는 이어지는 전송마다 한 번만 판단하므로 하나하나가 다른 근거다. 묶지 않는다.
  // 서버는 근거를 받은 순서대로 주므로 뒤에 있을수록 최근 것이다.
  function groupEvidence(evidence) {
    const groups = [];
    let radar = null;
    for (const e of evidence) {
      if (e.source !== "radar") {
        groups.push({ source: e.source, latest: e, history: [] });
      } else if (!radar) {
        radar = { source: "radar", latest: e, history: [] };
        groups.push(radar);
      } else {
        radar.history.unshift(radar.latest); // 이전 갱신은 최근 것부터
        radar.latest = e;
      }
    }
    return groups;
  }

  function reasonList(reasons) {
    const list = element("ul", "reasons");
    for (const reason of reasons) list.append(element("li", "", reason));
    return list;
  }

  // options.history: 이전 갱신을 펼쳐 볼 수 있게 한다 (경보 상세)
  // options.historyOpen, options.onToggle: 다시 그려도 펼침 상태가 유지되도록 화면이 들고 있는 값
  function evidenceList(alert, options = {}) {
    const list = element("ul", "evidence");
    for (const group of groupEvidence(alert.evidence)) {
      const item = element("li", "evidence-group");
      const head = element("div", "evidence-head");
      head.append(element("span", "source", `${SOURCE[group.source]} ${group.latest.score.toFixed(2)}`));
      if (group.history.length) head.append(element("span", "updates", `갱신 ${group.history.length + 1}회`));
      item.append(head, reasonList(group.latest.reasons));

      if (options.history && group.history.length) {
        const details = element("details", "evidence-history");
        details.open = Boolean(options.historyOpen);
        details.addEventListener("toggle", () => {
          if (options.onToggle) options.onToggle(details.open);
        });
        details.append(element("summary", "", `이전 갱신 ${group.history.length}건`));
        const history = element("ol", "history");
        for (const e of group.history) {
          history.append(element("li", "", [e.score.toFixed(2), ...e.reasons].join(" · ")));
        }
        details.append(history);
        item.append(details);
      }
      list.append(item);
    }
    return list;
  }

  // ackState: 저장소의 확인 처리 상태 (없음 | "pending" | "failed")
  // 화면을 다시 그려도 처리 중·실패 표시가 유지되도록 버튼 상태를 저장소 값으로 정한다
  function ackControls(alert, ackState, onAck) {
    const box = element("div", "actions");
    if (alert.status !== "unacked") {
      box.append(element("span", "done", `✓ 확인됨 ${dateTime(alert.acked_at)}`));
      return box;
    }
    const pending = ackState === "pending";
    const button = element("button", "ack", pending ? "처리 중…" : "확인 처리");
    button.type = "button";
    button.disabled = pending;
    button.addEventListener("click", (event) => {
      event.preventDefault(); // 링크 안에 있어도 화면을 옮기지 않는다
      event.stopPropagation();
      onAck(alert.alert_id);
    });
    box.append(button);
    if (ackState === "failed") {
      const error = element("span", "error", "확인 처리에 실패했습니다. 다시 눌러 주세요.");
      error.setAttribute("role", "alert");
      box.append(error);
    }
    return box;
  }

  // ---------- 목록 갱신 ----------

  // 목록을 items 순서에 맞추되, 내용(sig)이 바뀐 항목만 새로 만든다.
  // 레이더 갱신이 2초마다 와도 다른 항목은 그대로 두어 버튼·선택 상태가 풀리지 않게 한다.
  function syncList(container, items, { key, sig, build }) {
    const existing = new Map();
    for (const node of container.children) existing.set(node.dataset.key, node);

    items.forEach((item, index) => {
      const itemKey = String(key(item));
      const itemSig = sig(item);
      let node = existing.get(itemKey);
      if (!node || node.dataset.sig !== itemSig) {
        const fresh = build(item);
        fresh.dataset.key = itemKey;
        fresh.dataset.sig = itemSig;
        if (node) node.replaceWith(fresh);
        node = fresh;
      }
      existing.delete(itemKey);
      const current = container.children[index];
      if (current !== node) container.insertBefore(node, current || null);
    });

    for (const node of existing.values()) node.remove();
  }

  RaFi.dom = {
    SVG_NS,
    GRADE,
    SOURCE,
    element,
    svgElement,
    link,
    clock,
    isToday,
    dateTime,
    hhmm,
    where,
    highestGrade,
    byPriority,
    byRecentUpdate,
    gradeBadge,
    groupEvidence,
    evidenceList,
    ackControls,
    syncList,
  };
})((window.RaFi = window.RaFi || {}));
