(() => {
  const workspace = document.querySelector(".workspace");
  const toggle = document.getElementById("sidebar-toggle");
  const key = "work-agent-sidebar-collapsed";

  // The page a link opens is the current page; the root serves several views,
  // so its links mark none.
  document.querySelectorAll("#sidebar-navigation a, #settings-nav").forEach((link) => {
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
    document.querySelectorAll("#sidebar-navigation > *, #settings-nav").forEach((item) => {
      if (collapsed) item.title = item.textContent;
      else item.removeAttribute("title");
    });
  };

  // The theme switch: the reader's choice for every page. theme.js keeps it
  // in the browser and sets it before each page draws. The button of the
  // theme the page shows is pressed.
  const themeButtons = [...document.querySelectorAll("[data-theme-choice]")];
  const shown = () => {
    const chosen = document.documentElement.dataset.theme;
    if (chosen) return chosen;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  };
  const showTheme = () => themeButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === shown()));
  });
  showTheme();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", showTheme);
  themeButtons.forEach((button) => button.addEventListener("click", () => {
    window.workAgentTheme.choose(button.dataset.themeChoice);
    showTheme();
  }));

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
