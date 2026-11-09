// The message of a refused request to this service, or `fallback` where the
// answer is not the service's JSON, such as a plain error page of a proxy.
// The form page, Files and Controls read each refusal through it.
const messageOf = async (answer, fallback) => (await answer.json().catch(() => null))?.message ?? fallback;
