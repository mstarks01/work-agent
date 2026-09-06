  fetch("/api/summary").then(r => r.json()).then(s => {
    document.getElementById("counts").textContent =
      s.waiting + " findings waiting, " + s.volatile +
      " of them found in some runs and not others.";
    const cases = Object.entries(s.by_case)
      .map(([id, n]) => id + ": " + n).join("   ");
    document.getElementById("cases").textContent = cases;
    document.getElementById("ledger").textContent =
      s.votes_recorded + " votes by " + (s.voters.join(", ") || "nobody") +
      "   |   " + s.pool + " findings in the reference pool" +
      "   |   " + s.double_voted + " answered by two people";
  });
