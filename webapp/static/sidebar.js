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
    document.querySelectorAll("#sidebar-navigation > *").forEach((link) => {
      if (collapsed) link.title = link.textContent;
      else link.removeAttribute("title");
    });
  };

  // The theme button: the reader's choice for every page, kept in the
  // browser. theme.js sets it before each page draws.
  const themeButton = document.getElementById("theme-toggle");
  const isDark = () => {
    const chosen = document.documentElement.dataset.theme;
    return chosen ? chosen === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  };
  const showTheme = () => themeButton.setAttribute("aria-pressed", String(isDark()));
  showTheme();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", showTheme);
  themeButton.addEventListener("click", () => {
    const theme = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    showTheme();
    try { localStorage.setItem("work-agent-theme", theme); } catch { /* Keep the theme for this page only. */ }
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
