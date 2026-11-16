// 해시 주소(#/, #/alerts, #/alerts/12)에 따라 화면을 바꾼다
//
// 화면(view)은 mount(root, params)로 그리고 { update(change), unmount?() }를 돌려준다.
// 저장소가 바뀌면 지금 보이는 화면의 update만 부른다.

(function (RaFi) {
  "use strict";

  const { element, link } = RaFi.dom;

  function create(root, routes, onRoute) {
    let current = null;

    function show() {
      const hash = location.hash || "#/";
      if (current && current.unmount) current.unmount();
      current = null;
      root.replaceChildren();

      for (const route of routes) {
        const match = hash.match(route.pattern);
        if (match) {
          current = route.view.mount(root, match.slice(1));
          onRoute(route.name);
          window.scrollTo(0, 0);
          return;
        }
      }
      root.append(element("p", "muted empty", "없는 화면입니다."), link("#/", "", "개요로 가기"));
      onRoute(null);
    }

    window.addEventListener("hashchange", show);
    show();

    return {
      update(change) {
        if (current) current.update(change);
      },
    };
  }

  RaFi.router = { create };
})((window.RaFi = window.RaFi || {}));
