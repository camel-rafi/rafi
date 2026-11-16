// 새 경보 알림: 탭 제목의 미확인 수, 새 L2 경보 소리
//
// 소리는 파일 없이 브라우저가 직접 만든다 (외부 파일을 쓰지 않는다).
// 브라우저는 사용자가 화면을 한 번 누르기 전에는 소리를 막으므로, 첫 클릭 때 소리를 준비한다.

(function (RaFi) {
  "use strict";

  const STORAGE_KEY = "rafi.sound";
  const TITLE = "RaFi 관리자";

  let enabled = loadSetting();
  let context = null;

  function loadSetting() {
    try {
      return localStorage.getItem(STORAGE_KEY) !== "off";
    } catch {
      return true; // 저장소를 못 쓰는 환경이면 켜 둔다
    }
  }

  function saveSetting() {
    try {
      localStorage.setItem(STORAGE_KEY, enabled ? "on" : "off");
    } catch {
      // 다음에 열 때 기본값(켜짐)으로 돌아갈 뿐이다
    }
  }

  function audio() {
    if (!context) {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return null;
      context = new AudioContext();
    }
    return context;
  }

  function unlock() {
    const ctx = audio();
    if (ctx && ctx.state === "suspended") ctx.resume();
  }

  function beep(times) {
    if (!enabled) return;
    const ctx = audio();
    if (!ctx || ctx.state !== "running") return;
    for (let i = 0; i < times; i += 1) {
      const start = ctx.currentTime + i * 0.25;
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.3, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.18);
      oscillator.connect(gain).connect(ctx.destination);
      oscillator.start(start);
      oscillator.stop(start + 0.2);
    }
  }

  function bindToggle(button) {
    const render = () => {
      button.setAttribute("aria-pressed", String(enabled));
      button.textContent = enabled ? "알림 소리 켜짐" : "알림 소리 꺼짐";
    };
    button.addEventListener("click", () => {
      enabled = !enabled;
      saveSetting();
      unlock();
      render();
      if (enabled) beep(1); // 켜졌는지 바로 들려준다
    });
    render();
  }

  function setUnackedCount(count) {
    document.title = count ? `(${count}) ${TITLE}` : TITLE;
  }

  // 실시간으로 받은 경보가 새로 L2가 됐으면(새 경보이거나 L1·L3에서 올라옴) 소리를 낸다.
  // 확인된 경보에 사건이 이어 붙는 갱신이나 이미 L2인 경보의 갱신에는 울리지 않는다
  function onAlert(previous, alert) {
    if (alert.status !== "unacked" || alert.grade !== "L2") return;
    if (previous && previous.grade === "L2") return;
    beep(3);
  }

  document.addEventListener("pointerdown", unlock, { capture: true });

  RaFi.notify = { bindToggle, setUnackedCount, onAlert };
})((window.RaFi = window.RaFi || {}));
