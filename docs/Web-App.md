# The web app

A local, single-user workspace for the analysis engine. Start with one system
description, select the installed frameworks, answer grouped questions, and
review the associated reports. See [First-Run](First-Run.md) for model setup.

```sh
uv run python webapp/main.py
```

Then <http://127.0.0.1:8000>.

It **embeds the service in process**: its jobs live in a service job store, and
the module that admits, stores and starts a job for the [`/v1` API](HTTP-API.md)
does the same for this app. With no store setting, the store is SQLite in the
app's data directory; see [Where the runs are kept](#where-the-runs-are-kept). It does not call `/v1` over HTTP,
so it needs no bearer token and no OIDC provider. It runs real models against
whatever you type, and every analysis costs whatever your vendor charges.

## Running it from a clone only

The app lives in a top-level `webapp/` directory and **never ships in the wheel**.
There is no `pip install` that gets you this app, no console script, and no
`[web]` extra — deliberately. `webapp/main.py` is a standalone driver script
outside `src/analysis_service`, the same as `examples/`, so no packaging
mechanism names it; that's independent of what the wheel bundles for the
*engine* itself (skills, prompts, config all ship with it — see
[Configuration](Configuration.md#config-paths-override-where-files-are-read-from)).
Run the workspace from the repository checkout; the engine remains separately
installable without frontend dependencies.

Its server, `uvicorn`, is in a `web` dependency group that `uv sync` installs by
default. Dependency groups never enter the built distribution, so if you embed
the engine you inherit none of this — your dependency surface stays `fastapi`,
`google-adk`, `litellm`, `pydantic` and `pyjwt[crypto]`.

## Browser tests

`tests/test_browser.py` serves the app and drives its pages in headless
Chromium. It needs the `browser` dependency group, which `uv sync` does not
install by default, and a Chromium build:

```bash
uv sync --group browser
uv run playwright install --with-deps chromium
uv run --group browser pytest tests/test_browser.py
```

Without them the browser tests skip. CI sets `REQUIRE_BROWSER=1`, so there a
missing browser fails the suite instead.

`--with-deps` installs Chromium's system libraries and needs root. On a server
where you have none, download the libraries it reports missing and point
Chromium at them:

```bash
ldd ~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell | grep "not found"
apt-get download <each package>        # no root needed
dpkg -x <package>.deb ~/.local/lib/chromium-deps
LD_LIBRARY_PATH=~/.local/lib/chromium-deps/usr/lib/x86_64-linux-gnu uv run --group browser pytest tests/test_browser.py
```

## What you select

**The frameworks to run**, one checkbox per framework this install carries. The
carried set comes from [`config/frameworks.toml`](../config/frameworks.toml).
Every box starts unticked, so tick each framework you want **Prepare analysis**
to run.
The app refuses a submission with no box ticked. Each framework you tick costs
its own nodes, so tick only what you need.

A framework that needs a job option gets a control beside its checkbox. ASVS
needs a level, because ASVS 5.0 tells your organization to choose one and no
description implies it. So the ASVS row carries a level select, and STRIDE, which
needs no options, carries none.

One submission gives you one report. Each framework you selected writes its own
block in it, and every block reads the same extracted model. So STRIDE threats
and ASVS requirement rulings arrive together, against one system, from one run.

The server decides what runs, not the page. A submission naming a framework this
install does not carry is refused, a framework named twice is refused, and the
block order is the config file's order rather than the page's. The rule is the
one the HTTP route and the engine read, so the three entry points agree. A submission that
leaves out an option its framework needs is refused before any model runs, with a
message naming the field.

The app reads each field of a submission by the rule `POST /v1/jobs` reads it
with, and refuses a body that `POST /v1/jobs` refuses. The refusal names the
field and the rule, such as a blank description or a label with a line break,
and never repeats the value. Each JSON route of the app refuses a field that
it does not read, as each `/v1` route does.

A paused run keeps the frameworks it selected. When the settings no longer
carry one of them, a continue of that run is refused with a message that names
the framework. You can still save answers on it, and the run continues when the
settings carry the framework again.

## What it shows you

**No model on the form.** The form shows no model, no credential status and no
sampling parameter. The models are the pair you selected in step 2 of
[First-Run](First-Run.md), since nothing is selected by default. The form
appears only after the credential check passes. The report carries the
**served** model build that answered each node.

**No input to an analysis can influence which model runs** — not a
form field, not a query parameter, not a header. The framework picker is not an
exception to this: it chooses what the app analyses, never what analyses it.
Model selection lives in
[`config/model_tiers.toml`](../config/model_tiers.toml) and the `ANALYSIS_MODEL_*`
overrides. To change a model, use the [Settings page](#the-settings-page). The
app has no authentication, so its save accepts only a request from the app's own
page on loopback: the same `Host` and `Sec-Fetch-Site` checks as each other
writing route.

**Progress, in four stages.** A STRIDE run takes around 40 seconds, and each
further framework adds its own lane nodes to that. The page shows a progress bar
of four stages and one status line, for example
`1 of 4 stages complete · Checking the system map for gaps or conflicting details…`.
The bar counts completed stages, not elapsed time. A run that stops for your
answers counts the extraction, the checks of the system model, the question
catalog and the pause. A run toward a report counts the extraction, the
preparation, the review of the findings and the report assembly. The status line
names the next stage in plain words, and the page shows no graph node name.

**The report** has three sections under its title: **Report**, **Questions**
and **About the analysis**. The page opens on **Report**, and `?view=questions`
or `?view=about` opens the other two.

**Report** holds one block per framework you selected. Each block starts with a
summary: the confirmed findings by severity, how many findings are conditional
on missing information, and how many the review dismissed. The report is
complete for what you told it, and the summary says that you can use it without
answering a question. Where two or more confirmed findings rest on one control
that the description states is missing, a line names that one shared action and
the findings it addresses. Each finding is one row: its severity, its title, its
next action and its ID. Click a row to open it. The open row gives the lane, the
confidence and the verdict, the facts a conditional finding needs, the reason a
dismissed finding was dismissed, the full attack narrative, the affected
components and flows, and the two recommendations. The heading over the
recommendations follows the verdict: **Recommended actions**, **Potential
mitigations · if confirmed**, or **Original proposed mitigations · not active
actions**. **Evidence and justification** holds the grounds: the quotes,
unknown attributes and boundary crossings the agent raised the finding on, with
a quote the service could not find in its source marked as such. The confirmed
findings show open; the conditional and the dismissed findings each sit in a
closed section. A name that an agent writes, such as an element ID, an
attribute or an asset tag, shows as words. The name itself shows when you hover
over the words.

**Questions** asks the facts that the conditional findings need, in the same
list as the questions before the analysis: a group for each kind of question,
an index of the groups, typed answers, and one shared answer for the parts you tick where a
question asks about several parts. The service keeps which parts that
answer covers. Each question says what became of it before, names
the findings that use it, and explains under its **?** what the answer informs.
A link from a finding opens its question, and a link from a question opens its
finding. **Save draft** keeps the answers and runs nothing. **Update report**
starts the report's one follow-up. A final report shows its answers here to
correct instead, and a correction runs nothing.

**About the analysis** starts with the extracted system model: its diagram,
its boundary crossings and assumptions, its gaps, and a table for each element
kind. Then come the reference of each question
and each framework's notes and diagnostics. Two closed sections hold the rest:
**How this report was made** has the report's identifiers, times and the
versions that ran, and **Pipeline** has the served-build provenance and
fingerprint of every LLM node.

**The system model diagram**, above the DFD tables, draws the model: a dashed
column for each trust zone, the elements inside the zone the model states, and
an arrow for each flow. The elements go down the page in the order the flows
reach them, so a flow inside a zone runs down its column and a flow between
zones runs across. A coloured arrow crosses a trust boundary. A dotted arrow
may cross one, because a zone is not stated. Each flow's name sits on its
arrow. An element whose zone the model does not state is drawn outside every
zone, in a column named **Zone not stated**, and an element whose zone the
service inferred says "zone assumed". Hover over an element or a flow to see
its whole name. **Download as Mermaid**, in the caption, saves the same model
as a Mermaid flowchart (`system-model.mmd`) to paste into a document that
renders Mermaid. A comment on its first line states which shape names which
element type. Each trust zone is a subgraph labelled "Trust zone", each arrow
names its flow and the protocol where the model states one, and a flow that
crosses a boundary, or may cross one, takes a link style. The file reads back
as a `diagram` source. Under the diagram, a table for each element kind (external entities, processes, data stores and data flows)
lists the same elements in words, with a column for each attribute. Hover over
a name to see its ID.

**Gaps in the system model**, in About the analysis, is a table of each
attribute that the description leaves unstated, with the count and the
elements it is unstated on. The
attribute on the most elements comes first. Where the report asks about an
element, an **Answer** link opens that question.

**The complete export** — **Preview complete export**, above the findings,
shows the whole report as one document: every finding open, each question with
its history and the findings it informs, and About the analysis as an appendix.
The export has no editor; it gives the report's address to answer the questions
online. **Print or save as PDF** prints it, and **Close preview** returns the
page to the section and the open rows you had. A print from the browser prints
the same export. A print leaves out the sidebar, the tabs and the buttons, and
it always uses the light colours.

## When the configuration is wrong

If the engine cannot be built — bad tier config, missing credentials, an
unsupported sampling parameter — the app still starts, but serves a **diagnostic
page instead of the form**. There is no textarea and no **Prepare analysis**
button, so no
analysis can run on a model nobody chose.

Where the config itself read cleanly — the credential and sampling cases — the
page names the vendor your config selects, states the credential mode that
vendor runs under, and lists **every** environment variable that vendor needs,
marking the ones that are unset. It reports presence only and never prints a
value. Vertex is shown because it needs more variables than most, which is what
makes the next point visible:

```
vertex
Credential mode: iam. This vendor passes no credential material. The platform
supplies the identity, and the vendor's SDK resolves it from the environment
this process runs in.

ANALYSIS_VERTEX_PROJECT   NOT SET
ANALYSIS_VERTEX_LOCATION  NOT SET
```

A vendor whose provider needs a client library gets a row for that too, naming
the extra that installs it. Without it the page would mark every variable set
while the run still failed to bind.

All of them at once, rather than one per restart — the underlying check raises on
the first variable it finds missing, which would otherwise mean two restarts to
discover two variables.

The mode is **reported, never resolved**. Under `iam` the only way to find out
whether an identity exists is to ask for one, which is a network call on page
render and reaches the instance metadata service. So the page says what your
deployment declared and what the platform has to supply, and leaves the answer
to a run.

If the tier config is what failed, there is no selected vendor to report and the
page says so instead: fix the file named in the error first.

The diagnostic page links to the [Settings page](#the-settings-page), which
serves also when the engine does not build.

**Recovery is a save on the Settings page.** When the save passes, the engine
builds at once and the form shows, with no restart. The app does not watch its
files or its environment: after a hand edit of a config file or of
`settings.env`, or a change to the process environment, restart the app.

## The Files page

**Files**, between **Analyses** and **Settings**, keeps **Owner File**s: call
transcripts and Mermaid diagrams that you add to any of your analyses
(ADR 0118). Each file stays until you delete it. Retention does not remove it.

- **The list.** A table gives the name, the kind, the format, the size and the
  date of each file, the newest first. A line above it gives the count against
  `max_owner_files`.
- **Search and filters.** As on the Analyses page, a search box filters the
  list by name, and two selects filter it by kind and by format. The selects
  list the values of the format table.
- **Upload a file.** The page reads the file in the browser with the checks of
  the direct upload, then a dialog asks for the name and the kind. The name
  starts as the file name. A plain text file is a "Transcript" or a
  "Description", and a Mermaid file is always a "Diagram". `POST
  /workspace/files` receives the text in JSON, with the name, the kind and the
  format, and checks it again: the kind that the format's row allows, the byte
  limit of one job, the control characters, the name rule of a Source label,
  and the diagram type. It refuses a name that the page gives its own
  Sources, such as "Uploaded transcript", a name you already use, and an
  upload over the count limit.
- **Delete.** A dialog says that each analysis that used the file keeps its
  own copy. `DELETE /workspace/files/{file_id}` removes the row and the text,
  and keeps nothing.

The analysis form shows "Files for this analysis": a checklist of your files,
a "Manage files" link and a size meter. The submission sends the chosen IDs
in `owner_files`, and the server copies each file's text into the job as a
Source with the file's name as its label (ADR 0119). A later delete or a new
upload changes no job and no report. When a chosen file is gone, the
submission is refused and the checklist shows the file as "Missing". An
analysis that you open again lists "Files used" from its own copies.

## The Settings page

**Settings**, beside **Analyses**, shows each Deployment Setting in one table
with three columns: Setting, Value and Source. A header row starts each group,
and the search field above the table filters by setting name. It serves also
when the engine does not build, and a banner then shows the loader's message.

The Source column shows where each value comes from, in this order:

| Source | Where the value comes from |
| --- | --- |
| process environment | A variable of the process. The row names the variable: unset it to change the value on the page. |
| this page | A line of `settings.env` in the settings directory. The row shows the shipped value beside it, or "none shipped". |
| the TOML file | The shipped file that its config path names, such as `config/resilience.toml`. |
| refused | The loader refuses the TOML file or its path. The row shows the loader's message in place of a value. |
| default | The value that the loader gives when no source sets one. |

The page shows only the rows that a loader reads now. The credential rows follow
the vendor of each tier and that vendor's credential mode, with the unsaved
changes applied, so one save sets a tier and its key. A vendor in the `iam`
mode shows a short note and its setup command. The job storage rows follow the
selected job store, object store, object credential mode and database, with
the unsaved changes applied, so one save sets a `sql` job store and the settings that it reads.
Each job storage row takes effect only after a restart. The thinking row of each tier follows the tier's model, with
the unsaved changes applied. For a model that takes a thinking level, it offers
"The model's own level", `low`, `medium` and `high`; the first choice removes a
saved level. For a listed model that takes none, it says so and offers to remove
a saved level. For a model that the list does not hold, it offers the levels and
says that the save checks the model. The row says that a change makes each run
uncertified until a sweep measures it. The other sampling rows and review
independence show "Shown only" and the reason.

The page can tell whether a model takes a thinking level, but not which levels
it takes: the pinned litellm checks the parameter and not its value. A save of a
level on a model that takes none is refused on the tier's thinking row.

A key never shows. A key row says "A key is saved. The page never shows it." or
"No key is saved.", and the page never opens the key file. A value that holds a
password in a URL is not shown.

The model field of each tier suggests the models that the pinned litellm map
lists for the vendor of the tier. A model that the map does not list shows a
note. The note is not a refusal: the map lags each new model.

Each text field that is not a key shows an example value that its loader
accepts, such as "e.g. us-central1". A field that no value passes shows "Not
set", for example the upstreams of a vendor that routes to one provider.

A line of `settings.env` that this version does not read shows with its key and
its line number. The startup refuses such a line, so the banner also names it.
"Remove the line" adds its removal to the unsaved changes.

### A save

Change a value in the table, and the panel beside it lists each unsaved change
with its old value and its new value. "Discard" drops them. "Reset to shipped"
on a value from this page that has a shipped value removes its line, so the
shipped value applies again. A value from the process environment cannot change
here: unset the variable first.

A key field only writes. Type a key to save it, or to replace the saved key. The
panel lists "a new key" and never the key.

"Check and save" sends the changes. The server checks each value against the
value rule of `settings.env`, and each key against the rule of a secret file
([File permissions](Configuration.md#file-permissions)). Then it builds a test
Deployment and a job store from the new settings with the loaders that the
startup uses. No check calls a provider. When a check fails, the app changes nothing, and the page lists one
message for each refusal. A message names the setting and the rule, and never
repeats the value.

The check reads each new key from a new key file in the settings directory.
When the checks pass, the app writes `settings.env` and applies the new
settings at once, with no restart. The write of `settings.env` commits the save:
when a check fails or a write fails, the app deletes the new key files, and the
settings that were in force stay in force. After the save:

- The next analysis runs on the new settings. An analysis that runs at the save
  finishes on the settings that started it.
- A change to the carried frameworks refuses a later answer to a paused analysis
  that selected a removed framework, with a message that names it.
- A change to `require_certified` or to a tier changes whether an older report
  shows, as a restart does.
- A job storage change does not apply until a restart. The page names each
  changed job storage setting and tells you to restart the app.
- A save that leaves no tier on a vendor removes the key line of that vendor
  and deletes its key file. The panel says so before the save.

The app makes the settings directory at the first save, with mode `0700`, and
writes `settings.env` and each key file with mode `0600`. Each key save writes
a key file with a new random name. After the write of `settings.env`, the app
deletes each key file that no variable names, also a file that a stopped save
left.
[Configuration](Configuration.md#the-settings-directory-of-the-first-run-app)
gives the name form of a key file. Each save writes one log line that names
the changed settings and no value.

The page loads with a revision of `settings.env`. A save from a tab that loaded
before another save, or before a hand edit of the file, is refused with a
message to reload the page, so a save never writes over a change it did not
see.

## Its limits, which are the point

- **Loopback only.** `127.0.0.1`, hard-bound, with no host flag and no override.
  The no-auth posture is only safe there: anything reachable is an
  unauthenticated proxy to your vendor bill. Remote authenticated access is what
  [`/v1`](HTTP-API.md) is for.
- **One run at a time.** A second submission while one is running is refused with
  a message rather than queued.
- **At most 20 runs.** The app holds at most 20 jobs, and it deletes the
  oldest finished job first. A job the app cannot read has finished. A
  job that waits for your answers is never deleted. When every held job waits
  for answers, a new submission is refused until you answer or continue one.
  A refused submission deletes nothing.
  A deleted job leaves the usage row the store's admission reads
  ([ADR 0066](adr/0066-a-deleted-job-leaves-a-usage-row.md)).
- **No accounts.** If you want the JSON, take it from the
  engine directly; [`examples/embed.py`](../examples/embed.py) is four lines from
  `report.model_dump_json()`.

## Where the runs are kept

The app keeps its runs in a job store. It reads the store settings of the
[`/v1` service](HTTP-API.md#job-storage) and builds the store as the service
does, with one difference: where a store setting is unset, the app gives a
default. With no store setting, the app keeps its runs in SQLite and a file
root in its data directory, `$XDG_DATA_HOME/work-agent`, or
`~/.local/share/work-agent` where `XDG_DATA_HOME` is unset
([ADR 0111](adr/0111-the-first-run-app-keeps-its-runs-in-sqlite-by-default.md)):

| Setting | Default |
| --- | --- |
| `ANALYSIS_JOB_STORE` | `sql` |
| `ANALYSIS_DATABASE_URL` | `sqlite+aiosqlite:///` and the data directory, then `/jobs.db` |
| `ANALYSIS_OBJECT_STORE` | `filesystem` |
| `ANALYSIS_OBJECT_ROOT` | the data directory, then `/objects` |

A default applies only where the selected rows read it. So with
`ANALYSIS_JOB_STORE=memory`, the app gives no database or object setting, and
with `ANALYSIS_OBJECT_STORE=s3`, it gives no object root. The Settings page
shows each default value, and a change there takes effect at the next start.

So a first run needs no setting and no command, and a restart keeps every
run. The `web` dependency group carries the `sql` extra, so `uv run` installs
the SQLite packages with the app. The app makes the data directory and the
object root with mode `0700` where they do not exist. It makes the database
file with mode `0600`, and migrates it itself
([ADR 0110](adr/0110-the-first-run-app-migrates-a-sqlite-job-store.md)).
Where the file is at an older revision, the app migrates it to the latest one
and logs one line that names the two revisions. A file at a revision that
this app does not know stops the startup. A data directory or a root that a
group or others can open also stops the startup. The app does not change its
mode.

To keep nothing on disk, select the `memory` store on the Settings page. A
restart then loses every run. A PostgreSQL database still needs
[the migration step](HTTP-API.md#the-migration-step) before the app starts.

An unusable setting stops the startup, and the app does not fall back to
another store. At startup, the app logs the store class it built.

The app starts in the order of [the service](HTTP-API.md#the-startup), and a
failed step stops the startup. The differences are the store defaults at the
first step, and the private directories and the SQLite migration before the
store is built. The app builds the store and runs these steps before it builds
the engine, so they also run when the app serves the diagnostic page. The
restart scan fails each run that was queued or running at the last stop, and
the run shows a message that names the restart. Submit that run again.

After a restart over a `sql` store, the list of analyses shows the runs from
before the restart. Each report opens, and your drafts, saved answers and
corrections stay. A run that waits for your answers can be answered.

The bound of 20 runs applies to each store. The app chooses the runs to delete
before it admits a new run, and it deletes them after the admission. A chosen
run that changes in that time stays, and the app then holds one run over the
bound. Each delete writes one log line with the cause `workspace`.

## How the report page is rendered

`webapp/report_view.html` is a self-contained renderer for the
[report schema](Report-Schema.md) — inline CSS and JS, no build step, no
dependencies. The app reads it, substitutes your run's JSON into its
`<script type="application/json" id="report">` block, and serves the result.

The page sits in the workspace layout: it carries the workspace styles, the
sidebar, and the **Description** and **Reports** tabs of the report's analysis.
**Description** opens the analysis at its description, and **Reports** opens
the list of its reports.

It holds no report of its own, so opening it from disk shows nothing. It is a
template belonging to this app, not a sample to look at.

An agent writes an element ID in backticks, because that is how the prompt hands
it over. The page reads a pair of backticks around a non-empty span as a `code`
element and renders no other Markdown, so a description names
`flow:process:web-api>store:orders>write` in the same face the element table does. A quote is
excluded: its text is the submitter's own words, and a backtick among them is one
of those words.

## How the pages are protected

Two rules, held over every page rather than only the report page.

**Untrusted text reaches the DOM as text.** Every value the submitter can
influence renders as `textContent` or as a constructed node, never by assigning
a string of markup. That includes the form page, which is not obvious: a source
label and a validator message both travel back to it over SSE, and neither is
escaped for markup on the way. There is no escape helper on any page, which is
what makes forgetting one impossible rather than merely unlikely.

**An uploaded transcript or diagram reaches the server as text, never as a
file.** The form page reads the file in the browser. It refuses a file larger
than the deployment's `max_source_bytes` before it reads a byte, then refuses
bytes that are not strict UTF-8, then refuses any control character other than
a tab or a line break. The file's extension finds its row in the format
table, `analysis_service.sources.FORMATS`, which the server renders into the
page with the `accept` value of the file field. A file with no row is refused,
and the upload takes the row's default kind: a `.txt`, `.vtt` or `.srt` file is
a `transcript`, and a `.mmd` or `.mermaid` file is a `diagram` (ADR 0120).
`POST /analyze` receives the text as a source in its JSON body, the same shape
as a pasted description, so no route parses a file, a file name or a content
type. The server renders the words of each refusal that it also makes into the
page, so the page and the server word a refusal in one way.

**Each write route reads its body through one reader,** `_json_body` in
`webapp/main.py`. It refuses a media type other than `application/json` with
`415`. It refuses a body over the route's limit with `413`, and it stops the
read at the first byte over the limit. The limit is `MAX_SAVE_BYTES` for the
two Settings routes. For each other route, the limit is twice the deployment's
`max_source_bytes`, the guard that `/v1` also applies.

**Every page carries a strict nonce CSP,** `default-src 'none'` with a fresh
per-response nonce on each inline block and no `'unsafe-inline'` anywhere. A
page declares what it does as a `Grants` in `webapp/page.py`, and its policy
follows from that declaration; a page that declares nothing is granted nothing.
Each policy grants only what its own page does:

| Page | Grants beyond `default-src 'none'` |
| --- | --- |
| Report | `script-src`/`style-src` nonce, plus `connect-src 'self'` for `/answer/{run}` and `/correct/{run}`. It loads nothing. |
| Form | the same, for `/example`, `/analyze`, `/answer/{run}`, `/amend/{run}`, `/events/{run}`, `/workspace/runs` and `/workspace/files`. |
| Diagnostic | `style-src` nonce only — it runs no script, so it is granted none. |
| Settings | `script-src`/`style-src` nonce, plus `connect-src 'self'` for `/settings` and `/settings/rows`. It draws its table from the data in the page. |
| Files | `script-src`/`style-src` nonce, plus `connect-src 'self'` for `/workspace/files`. |

`base-uri` and `form-action` are `'none'` everywhere; the form posts through
fetch, so a navigation away from it would be something going wrong. A page and
its policy are built together and served together, so serving one without the
other is not something the code can express.

Every response also carries `X-Content-Type-Options: nosniff` and
`Referrer-Policy: no-referrer` — those are per response rather than per page,
which is why they are not part of the CSP.

Loopback binding is the first of these and not the whole of them: a page you visit can reach a loopback port, and DNS rebinding is how it tries. Three controls stop it, and each refuses something the others do not — the `Host` check refuses a rebound name, the `Sec-Fetch-Site` check refuses a write that did not come from this page, and `frame-ancestors 'none'` refuses a page that would frame this one to borrow its origin. `webapp/page.py` holds all three. On
`127.0.0.1` the submitter is both attacker and victim. These are the controls
that keep that from being the only thing standing between the two.

## Grouped workspace

Use **New analysis** to enter a name and a system description. The framework
picker is generated from installed packages, including their declared options.
Frameworks start unselected; questions are enabled by default. Read **How questions work** on the description page before preparing an analysis. Each question group starts collapsed. Click its heading,
or its link in the group index, to open it. The group index jumps to a group without
changing its answers or the engine's question order. Shared answers, row
exceptions, earlier answers, skipped questions and follow-up rules use the
existing answer service.

**Description**, **Questions** and **Reports** belong to the same analysis.
Use **Analyses** to reopen a paused analysis or review its retained reports.
Reopening a running analysis polls a read-only snapshot, leaving its original
progress stream alone. An analysis that the app cannot read, because another
schema version stored it or a stored value is one the app no longer lists,
stays in the list. A run whose stored value the app no longer lists shows as
**Cannot be read**. Its pages return `409` and ask you to submit it again. Names are limited to 200 characters on one line.

The list shows each analysis's name, creation date and status, and can be
sorted by name or date. The search box filters the list by the shown name, the
system name and the frameworks.
The status and framework selects filter it more.

- **Rename** gives the analysis a title. The list shows the title in place of
  the system name, and a blank title shows the system name again. The report
  keeps the system name that you submitted
  ([ADR 0106](adr/0106-a-workspace-title-is-the-workspaces-own-name-and-the-report-keeps-its-system-name.md)).
- **Stop** shows on an analysis that is in progress, and the progress panel
  shows **Stop analysis**. A stop ends the run at once, and the analysis shows
  **Stopped**. The run makes no report, and the tokens it used stay counted.
  A stopped follow-up gives its questions back to the paused run, so you can
  answer them again. A run that ends before the stop reaches it ends as it
  ran
  ([ADR 0109](adr/0109-a-person-can-stop-a-run-and-the-job-ends-stopped.md)).
- **Delete** removes the analysis with each of its reports, after you
  confirm. You cannot delete an analysis while it runs. Stop it first. If an answer in
  another tab starts a job of the analysis during the delete, the delete
  stops with the same message and says how many of its jobs it deleted
  first. The analysis stays in the list
  ([ADR 0107](adr/0107-a-delete-keeps-a-job-whose-child-is-in-flight.md)).
  The app keeps a usage
  row for each deleted job, so a delete does not reset the usage limits
  ([ADR 0066](adr/0066-a-deleted-job-leaves-a-usage-row.md)).
- Select analyses with the checkboxes, then use **Delete selected**. The
  checkbox in the header selects each analysis that the filters show. The page
  lists each analysis that it could not delete, with the reason.

The bound of 20 runs still applies. With the `memory` store, a restart clears
everything; with a `sql` store, the analyses stay
([Where the runs are kept](#where-the-runs-are-kept)). The UI does not write
descriptions or answers to browser storage. Accounts and authorization between
users require separate backend work; the workspace is still bound to
loopback.

## Frontend security controls

Keep untrusted names, descriptions, question text and messages in text nodes.
The workspace uses native DOM construction, no HTML-string rendering or
external scripts. Keep nonce-based CSP, frame protection, no-store responses,
host validation and same-origin checks on all write routes. These follow the
[OWASP XSS prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html),
[CSP](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
and [CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
guidance. CSP supplements text-only rendering; it does not replace it.

Run the offline workspace and browser tests to cover hostile names, same-origin
write refusal, question revisions, reopening runs and responsive layouts.
Treat this as a set of tested controls, not an OWASP certification. Do not
expose this unauthenticated app to a network; introduce authenticated ownership
checks for every analysis and report before adding multi-user access.

The app has one local operator. Every job it holds belongs to one **Principal**
under the reserved issuer `local:`, which no bearer token can name. The first
analysis creates that principal; before it, the app lists no runs.


### Question progress and reports

The number of recommended questions can rise as well as fall when an answer
lists a part or removes a question, and the page says so.

Each answer is saved as it is given, one request at a time for the run, with
the revision the page read. A chosen option is sent at once; typed text waits
400 ms after the last keystroke. The save line says something only on an error:
**Not saved** when a save fails, with **Save now** to send it again, or **Not
saved: an answer changed elsewhere** beside a conflict. A stale
revision makes the page read the answers again: an edit the other tab did not
touch is sent again, and an edit over an answer that changed elsewhere is shown
beside that answer with **Keep mine** and **Take theirs**. A lost response is
read back the same way before anything is sent again, and **Start the analysis**
saves what is pending first and starts once. An edit over an answer another tab
removed waits for the submitter in the same way. A save larger than one request
goes in batches at the `answer_limits` the service publishes. Leaving the page
with an unsaved edit sends it, and the browser asks to confirm. A new row the
service ranks above the editor that has the focus waits until the focus leaves
it. Where the service refuses answers that would leave a part present under an
absent ancestor, its refusal names both questions, and the page offers to answer
the other one the same way in one save. A row the list no longer holds shows no
**Skip for now**, because the service takes a skip only of a listed question.
The job store holds the answers. A `sql` store keeps them across a restart,
and the `memory` store loses them when the app stops.

When no recommended question remains, **Start the analysis** becomes the primary
action. Skipped questions remain open facts, not negative answers.

Reopening a running analysis shows its progress bar and status line from its
session snapshot, without consuming its live progress stream. Before the first
stage completes, the line reads `0 of 4 stages complete` and names the current
stage. A completed analysis opens its
report directly. Report entries use the analysis name and generation timestamp;
the report header labels its generation time in UTC.

Question headings use sentence case. Keep original component names and acronyms
in question text; do not title-case model data. Encryption questions use the
model's structured flow endpoints rather than parsing arrow labels or IDs.
