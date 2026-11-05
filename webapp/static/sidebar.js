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
    // A collapsed link or button shows only its icon, so its title names it
    // on hover.
    document.querySelectorAll("#sidebar-navigation > *").forEach((item) => {
      if (collapsed) item.title = item.textContent;
      else item.removeAttribute("title");
    });
  };

  // The theme button: the reader's choice for every page. theme.js keeps it
  // in the browser and sets it before each page draws.
  const themeButton = document.getElementById("theme-toggle");
  const isDark = () => {
    const chosen = document.documentElement.dataset.theme;
    return chosen ? chosen === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  };
  const showTheme = () => themeButton.setAttribute("aria-pressed", String(isDark()));
  showTheme();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", showTheme);
  themeButton.addEventListener("click", () => {
    window.workAgentTheme.choose(isDark() ? "light" : "dark");
    showTheme();
  });

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
