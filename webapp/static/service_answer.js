// The JSON body of an answer, or null where the answer is not JSON, such as a
// plain error page of a proxy. Each page reads each body through it.
const jsonOf = (answer) => answer.json().catch(() => null);

// The message of a refused request to this service, or `fallback` where the
// answer is not the service's JSON, such as a plain error page of a proxy.
// The form page, Files, Controls and the report page read each refusal
// through it.
const messageOf = async (answer, fallback) => (await jsonOf(answer))?.message ?? fallback;
