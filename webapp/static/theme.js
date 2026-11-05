// The theme the reader chose on the sidebar, set before the page draws, so no
// page shows the other theme first. With no choice, the system setting stays.
try {
  const theme = localStorage.getItem("work-agent-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch { /* The system setting stays. */ }
