(() => {
  const workspace = document.querySelector(".workspace");
  const toggle = document.getElementById("sidebar-toggle");
  const key = "work-agent-sidebar-collapsed";

  // The page a link opens is the current page; the root serves several views,
  // so its links mark none.
  document.querySelectorAll("#sidebar-navigation a").forEach((link) => {
    if (link.pathname !== "/" && link.pathname === location.pathname) {
      link.setAttribute("aria-current", "page");
    }
  });

  const setCollapsed = (collapsed) => {
    workspace.classList.toggle("sidebar-collapsed", collapsed);
    toggle.setAttribute("aria-expanded", String(!collapsed));
    const label = collapsed ? "Expand sidebar" : "Collapse sidebar";
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  };

  try {
    setCollapsed(localStorage.getItem(key) === "true");
  } catch {
    setCollapsed(false);
  }
  toggle.addEventListener("click", () => {
    const collapsed = !workspace.classList.contains("sidebar-collapsed");
    setCollapsed(collapsed);
    try { localStorage.setItem(key, String(collapsed)); } catch { /* Keep the current page state. */ }
  });
})();
