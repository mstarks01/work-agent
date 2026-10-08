// The answer limits the service admits (answer_round.ANSWER_LIMITS), handed to
// every page that takes answers.
const ANSWER_LIMITS = JSON.parse(document.getElementById("answer_limits").textContent);
// The submitter's own words beside a closed answer: an exception or a scope the
// answer alone would overstate (FactAnswer.detail, ADR 0073). Both answer
// editors build it here; each decides when its page sends it.
const answerDetail = (value) => {
  const detail = document.createElement("input");
  detail.type = "text";
  detail.maxLength = ANSWER_LIMITS.detail;
  detail.className = "answer-detail";
  detail.placeholder = "exceptions or detail (optional)";
  detail.value = value;
  return detail;
};
