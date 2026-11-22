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

  const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

  // 같은 날인지 비교하는 열쇠 (서버가 아니라 이 컴퓨터의 시간대 기준)
  function dayKey(iso) {
    const time = new Date(iso);
    return [time.getFullYear(), time.getMonth() + 1, time.getDate()]
      .map((n) => String(n).padStart(2, "0"))
      .join("-");
  }

  // "오늘 · 11월 16일 (월)", "어제 · 11월 15일 (일)", "11월 14일 (토)"
  function dayLabel(iso) {
    const time = new Date(iso);
    const date = `${time.getMonth() + 1}월 ${time.getDate()}일 (${WEEKDAYS[time.getDay()]})`;
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    if (isToday(iso)) return `오늘 · ${date}`;
    if (time.toDateString() === yesterday.toDateString()) return `어제 · ${date}`;
    return date;
  }

  // "방금", "3분 전", "2시간 전". 오늘이 아니면 빈 문자열 (날짜가 이미 보이므로)
  function relative(iso, now = Date.now()) {
    const minutes = Math.floor((now - new Date(iso).getTime()) / 60000);
    if (minutes < 1) return "방금";
    if (minutes < 60) return `${minutes}분 전`;
    if (isToday(iso)) return `${Math.floor(minutes / 60)}시간 전`;
    return "";
  }

  function relativeSuffix(iso) {
    const text = relative(iso);
    return text ? ` · ${text}` : "";
  }

  // 목록의 시각: 시각(오늘이 아니면 날짜까지) + 상대 시각.
  // 상대 시각은 화면을 다시 그리지 않고 refreshRelative()가 주기적으로 바꾼다
  function timeLabel(iso, className = "time") {
    const node = element("span", className, dateTime(iso));
    node.title = new Date(iso).toLocaleString("ko-KR");
    const rel = element("span", "rel", relativeSuffix(iso));
    rel.dataset.time = iso;
    node.append(rel);
    return node;
  }

  function refreshRelative(root = document) {
    for (const node of root.querySelectorAll(".rel[data-time]")) {
      node.textContent = relativeSuffix(node.dataset.time);
    }
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
  // 레이더는 사건이 이어지는 동안 2초마다 같은 event_id로 갱신을 보내므로 같은 사건의 근거가 계속 쌓인다.
  // 레이더 근거는 사건(event_id)별로 묶어 가장 최근 것만 보이고, 갱신 횟수와 사건 구간을 붙인다.
  // 한 경보에 레이더 사건이 여럿일 수 있다: 같은 구역에서 융합 시간창(120초) 안에 다시 일어난 사건은
  // 서버가 같은 경보에 붙인다. 이때는 "레이더 사건 1, 2"로 나눠 보인다.
  // event_id가 없는 예전 근거는 구간이 이어지면(SAME_EVENT_GAP_MS 안) 같은 사건으로 본다.
  // RF는 이어지는 전송마다 한 번만 판단하므로 하나하나가 다른 근거다. 묶지 않는다.
  // 서버는 근거를 받은 순서대로 주므로 뒤에 있을수록 최근 것이다. 묶음은 사건 시작 순서로 보인다.
  const SAME_EVENT_GAP_MS = 2000; // 레이더 탐지기의 merge_gap_s와 같은 값

  function groupEvidence(evidence) {
    const groups = [];
    const byEventId = new Map();
    let lastRadar = null;
    for (const e of evidence) {
      if (e.source !== "radar") {
        groups.push(newGroup(e, `${e.source}-${groups.length}`));
        continue;
      }
      let group = null;
      if (e.event_id) group = byEventId.get(e.event_id) || null;
      else if (continuesWithoutId(lastRadar, e)) group = lastRadar;

      if (!group) {
        group = newGroup(e, e.event_id || `radar-${groups.length}`);
        groups.push(group);
        if (e.event_id) byEventId.set(e.event_id, group);
      } else {
        group.history.unshift(group.latest); // 이전 갱신은 최근 것부터
        group.latest = e;
        // 시간대 표기가 달라도 맞게 비교하도록 시각으로 바꿔 비교한다
        if (new Date(e.event_start) < new Date(group.start)) group.start = e.event_start;
        if (new Date(e.event_end) > new Date(group.end)) group.end = e.event_end;
      }
      lastRadar = group;
    }
    groups.sort((a, b) => new Date(a.start) - new Date(b.start)); // 시작 순서 (같으면 받은 순서)
    const radar = groups.filter((g) => g.source === "radar");
    if (radar.length > 1) radar.forEach((g, i) => (g.label = `레이더 사건 ${i + 1}`));
    return groups;
  }

  function newGroup(e, key) {
    return {
      key, // 다시 그려도 같은 묶음을 찾을 수 있는 값 (펼침 상태 유지용)
      source: e.source,
      label: SOURCE[e.source],
      eventId: e.event_id || null,
      latest: e,
      history: [],
      start: e.event_start,
      end: e.event_end,
    };
  }

  function continuesWithoutId(group, e) {
    if (!group || group.eventId) return false;
    return new Date(e.event_start) - new Date(group.end) <= SAME_EVENT_GAP_MS;
  }

  // 46초, 1분 12초, 1시간 3분
  function duration(ms) {
    const seconds = Math.max(0, Math.round(ms / 1000));
    if (seconds < 60) return `${seconds}초`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return seconds % 60 ? `${minutes}분 ${seconds % 60}초` : `${minutes}분`;
    return minutes % 60 ? `${Math.floor(minutes / 60)}시간 ${minutes % 60}분` : `${Math.floor(minutes / 60)}시간`;
  }

  // 14:02:11~14:02:57 (46초)
  function eventSpan(start, end) {
    return `${clock(start)}~${clock(end)} (${duration(new Date(end) - new Date(start))})`;
  }

  // 가장 최근에 갱신된 레이더 사건 묶음 (진행 중 표시용)
  function latestRadarGroup(alert) {
    const last = [...alert.evidence].reverse().find((e) => e.source === "radar");
    if (!last) return null;
    return groupEvidence(alert.evidence).find((group) => group.latest === last) || null;
  }

  // "진행 중 · 46초째". 길이는 레이더가 보낸 사건 시작·끝으로 재므로 시계 차이와 상관없다
  function ongoingTag(alert) {
    const group = latestRadarGroup(alert);
    const length = group ? ` · ${duration(new Date(group.end) - new Date(group.start))}째` : "";
    return element("span", "ongoing", `진행 중${length}`);
  }

  function reasonList(reasons) {
    const list = element("ul", "reasons");
    for (const reason of reasons) list.append(element("li", "", reason));
    return list;
  }

  // options.history: 이전 갱신을 펼쳐 볼 수 있게 한다 (경보 상세)
  // options.openHistory: 펼친 묶음의 key 모음 (Set). 다시 그려도 펼침 상태가 유지되도록 화면이 들고 있는다
  function evidenceList(alert, options = {}) {
    const list = element("ul", "evidence");
    for (const group of groupEvidence(alert.evidence)) {
      const item = element("li", `evidence-group ${group.source}`);
      const head = element("div", "evidence-head");
      head.append(element("span", "source", `${group.label} ${group.latest.score.toFixed(2)}`));
      if (group.history.length) head.append(element("span", "updates", `갱신 ${group.history.length + 1}회`));
      head.append(element("span", "span", eventSpan(group.start, group.end)));
      item.append(head, reasonList(group.latest.reasons));

      if (options.history && group.history.length) {
        const details = element("details", "evidence-history");
        details.dataset.key = group.key;
        details.open = Boolean(options.openHistory && options.openHistory.has(group.key));
        details.append(element("summary", "", `이전 갱신 ${group.history.length}건`));
        const history = element("ol", "history");
        for (const e of group.history) {
          history.append(element("li", "", [clock(e.event_end), e.score.toFixed(2), ...e.reasons].join(" · ")));
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
    dayKey,
    dayLabel,
    relative,
    timeLabel,
    refreshRelative,
    where,
    highestGrade,
    byPriority,
    byRecentUpdate,
    gradeBadge,
    groupEvidence,
    duration,
    eventSpan,
    latestRadarGroup,
    ongoingTag,
    evidenceList,
    ackControls,
    syncList,
  };
})((window.RaFi = window.RaFi || {}));
