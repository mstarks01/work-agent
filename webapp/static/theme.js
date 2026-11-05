// The theme the reader chose on the sidebar, set before the page draws, so no
// page shows the other theme first. With no choice, the system setting stays.
// Only this file reads or writes the stored choice. The sidebar button calls
// workAgentTheme.choose.
window.workAgentTheme = (() => {
  const key = "work-agent-theme";
  const themes = ["light", "dark"];
  const apply = (theme) => { document.documentElement.dataset.theme = theme; };
  try {
    const stored = localStorage.getItem(key);
    if (themes.includes(stored)) apply(stored);
  } catch { /* The system setting stays. */ }
  return {
    choose(theme) {
      if (!themes.includes(theme)) return;
      apply(theme);
      try { localStorage.setItem(key, theme); } catch { /* Keep the theme for this page only. */ }
    },
  };
})();
