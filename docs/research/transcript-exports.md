# What real transcript exports look like (wayfinder #51)

**Question.** What does an analyst↔developer interview transcript actually look like when it
reaches a front-end, and what does that imply for our caps (#52) and our extraction guidance
(#53, #54, #56)?

**Method.** Two kinds of evidence, kept separate throughout:

1. **Measured** — four *real* Microsoft Teams transcript `.vtt` exports (225 minutes of
   technical meetings, 496 KB raw) pulled from a public repo and analysed byte-by-byte, plus
   one real Zoom `.vtt` (`GMT20260428-065744_Recording.transcript.vtt`, a 44-minute client
   interview). Every number below with a byte or percentage in it comes from these files.
2. **Documented** — vendor and standards documentation (W3C WebVTT, Microsoft, Zoom, Otter)
   for the parts that cannot be measured from a sample, chiefly *when* speaker attribution is
   present and *what it means*.

---

## TOP-LINE VERDICT

**Five findings, each of which lands on a live ticket.**

1. **A raw 60-minute meeting VTT does not fit in today's 100 KiB cap. Cleaned, it fits four
   times over.** All four real Teams exports are 108–144 KB raw; every one of them exceeds
   `MAX_DESCRIPTION_BYTES` (`jobs.py:68`, 100 × 1024 = 102,400). The same content as plain
   `Speaker: text` is 37–51 KB. **65% of a raw Teams VTT is machinery** — cue ids, timestamps
   and `<v>` tags — carrying zero extraction signal.

2. **We cannot state the cap in bytes and call it done (#52).** "100 KiB" means *55 minutes*
   of conversation if the front-end cleans, and *45 minutes* if it doesn't — a 2.9× swing on
   an axis the caller never sees. The cap needs to be paired with a stated expectation about
   what is submitted, or the rejection message is uninterpretable.

3. **Speaker attribution is reliably present but is an *identity* label, not a *role* label
   (#53).** Teams emits `<v Nicolas Blank>` on 100% of cues; Zoom emits `kadowaki-tch:`. Both
   come from the meeting *connection* — the account display name — not from voice analysis.
   So the label is trustworthy about **which participant** spoke, unreliable about **who they
   are** (`kadowaki-tch` is a handle), and silently wrong when two people share one microphone.
   **Extraction cannot infer analyst-vs-developer from the transcript.**

4. **Cue boundaries are not sentence boundaries, and a naive line-per-cue conversion produces
   broken text (#54).** 74% of consecutive Teams cues repeat the previous speaker; only 57%
   end on sentence punctuation. The median cue is 10 words — a fragment. Merging consecutive
   same-speaker cues collapses 736 cues into 194 turns and is the difference between prose and
   confetti.

5. **ASR emits no uncertainty markers at all — it emits fluent, confident, occasionally
   fabricated text (#53).** `[inaudible]`/`[crosstalk]` are *human* transcription conventions
   (Rev's style guide), absent from every machine export measured. WebVTT has no confidence
   field, and neither Teams nor Zoom writes one. Whisper's documented failure mode on silence
   and disfluency is *hallucination*, not omission. There is nothing for extraction to read as
   "the speaker was unclear here."

---

## 1. The export shapes

| Source | Format out | Speaker labels | Timestamps | Notes |
|---|---|---|---|---|
| **Teams** | `.vtt`, `.docx` | Yes — `<v Name>` voice spans on every cue | Per cue | Cue ids are `<meeting-guid>/<n>-<m>`; CRLF line endings |
| **Zoom** | `.vtt` | Yes — `Name: ` inline in the payload; `Unknown Speaker:` where unattributed | Per cue | Numeric cue ids; **no punctuation or capitalisation** from the ASR |
| **Google Meet** | Google Doc → `.docx`/`.txt`/PDF/RTF | Yes — paragraph-leading speaker name | Every 30–60 s, not per utterance | Never a caption format; already close to plain prose |
| **Otter** | `.txt`, `.docx`, `.pdf`, `.srt` | Optional toggle at export | Optional toggle; inline `M:SS` | Caller chooses whether either survives |
| **Granola / Fathom** (via exporters) | Markdown | Yes — `**Me:**` / `**Them:**` (Granola), real names (Fathom) | Varies | Notes and transcript are separate sections/files |
| **Generic SRT** | `.srt` | **No field for it** — only by inline convention | Per cue | Sequence + `HH:MM:SS,mmm` |

### Real Teams cue

```
5fd7f1de-6442-4b37-a79a-7f43656096ca/13-0
00:00:04.489 --> 00:00:10.769
<v Nicolas Blank>Hi everyone and welcome to Tuesday and
today we are doing a lunch and learn part</v>
```

Note the payload wraps mid-sentence across two *display lines* inside one cue, and the
sentence itself continues into cue `13-1`.

### Real Zoom cue

```
42
00:08:24.670 --> 00:08:45.189
kadowaki-tch: それでは、高度化の話のところをちょっと聞きたいと思ってて。…
```

Zoom puts the speaker inside the payload as bare `Name: ` text — there is no markup to strip,
and no way to distinguish a speaker label from a colon that happens to start an utterance.
The label is the Zoom display name, here an account handle.

### WebVTT structure (W3C)

`WEBVTT` magic string, then blocks separated by blank lines. Each cue is an optional id line,
a timing line `HH:MM:SS.mmm --> HH:MM:SS.mmm`, then payload. `NOTE` blocks are comments.
Payload may carry `<v Speaker>`, `<c>`, `<i>`, `<b>`, `<u>`, `<ruby>`, `<lang>`. **There is no
confidence, no channel, and no role attribute anywhere in the format.**

---

## 2. What survives a naive conversion to plain text

Measured on the four real Teams exports (225 min):

| | bytes | % of raw |
|---|---:|---:|
| Raw `.vtt` | 496,660 | 100% |
| Cue id lines (GUID/n-m) | 141,770 | **28.5%** |
| Timing lines | 99,851 | **20.1%** |
| `<v …>` / `</v>` tags | 70,178 | **14.1%** |
| **Spoken words** | **172,754** | **34.8%** |
| As `Speaker: turn` plain text (merged) | 181,889 | 36.6% |

The clean-text fraction is strikingly stable: 33.7%, 34.3%, 35.1%, 35.6% across four files.
**Roughly one third of a Teams VTT is content; two thirds is machinery.**

Three things are lost or mangled by naive stripping, in decreasing order of importance:

- **Turn structure**, if lines are emitted per cue rather than per speaker turn (see §4).
- **`NOTE` blocks.** WebVTT comments can carry exactly the context extraction most wants —
  one sample in the wild opens with `NOTE Attendees: Marc Dubois (CTO), Léa Martin (PM),
  David Okafor (Eng Lead)`. A strip-the-non-cue-lines converter throws the roles away.
- **Nothing else of value.** Timestamps and cue ids are pure noise for our purposes; there is
  no argument for preserving them.

---

## 3. Diarisation: present, but it answers a narrower question than we want

**Is speaker attribution reliably present?** In the measured corpus, yes — 100% of Teams cues
carried `<v Name>` with a real display name (4–6 distinct speakers per meeting); the Zoom
sample carried a label on every cue. Teams and Google Meet documentation both describe speaker
names as a standard part of the transcript.

**In what form?** Real names or account handles, not `SPEAKER 1`. But that is because the
label comes from the **meeting connection**, not from the audio:

- **Teams**: attribution is per participant stream. For *in-room* participants sharing one
  device, Microsoft requires a Teams Rooms Pro licence, an approved intelligent-speaker device,
  **and per-user voice-profile enrolment**; without it, unenrolled in-room speakers collapse to
  `Speaker 1 (Room name)`.
- **Zoom**: labels come from the participant's display name; unattributed segments are literally
  `Unknown Speaker`, editable after the fact in the web portal.

**Can we assume an analyst and a developer are distinguishable?** *Distinguishable*, yes, in
the ordinary case of two people on two connections. **Identifiable by role, no.** Nothing in
any export says which label is the analyst. The label may be a handle (`kadowaki-tch`), two
people on one mic share one label, and a dial-in appears as a phone number.

**Implication for #53:** the "whose statement is a fact" question cannot be resolved by rule
from the transcript. Either extraction treats all speakers symmetrically, or the *caller*
supplies the role mapping out of band — and the `sources` contract has an obvious place to put
it, since `label` is already required, unique and model-visible (#50).

---

## 4. Timestamp density and cue fragmentation

Per-cue timestamps plus cue ids are **48.6%** of a raw Teams VTT — the single largest cost in
the file, and entirely noise. Zoom's numeric ids are far cheaper than Teams' GUIDs but the
timing lines are the same size.

Fragmentation, measured on the 736-cue Data Deep Dive transcript:

- **73.7%** of consecutive cues repeat the previous speaker.
- Only **56.7%** of cues end on `.`, `?` or `!`.
- Median cue: **10 words** (mean 9.6).
- Merging consecutive same-speaker cues: 736 cues → **194 turns**, median 11 words, mean 37,
  max 1,667 (a presentation monologue).

So a converter that emits one line per cue hands extraction ~700 sentence fragments with the
speaker name repeated ~540 times unnecessarily. A converter that merges turns hands it 194
paragraphs. **This is a front-end concern, not ours** (file parsing is out of scope for this
map) — but it directly shapes what #54's prompt rendering will actually receive, and it is the
strongest candidate for integrator-facing guidance, which is likewise out of scope here and
noted in the map's Out-of-scope section.

---

## 5. Length: words and bytes

Measured, four real technical meetings, 225 minutes total:

| | rate | 30-min meeting | 60-min meeting |
|---|---:|---:|---:|
| Words | **142 wpm** | ~4,300 | ~8,500 |
| Clean text | **769 B/min** | **~23 KB** | **~46 KB** |
| Raw Teams `.vtt` | **2,212 B/min** | **~66 KB** | **~133 KB** |

Per-file: 50.9 min / 112,082 raw / 37,805 clean · 55.9 / 108,026 / 37,085 · 58.4 / 143,631 /
51,195 · 59.3 / 132,921 / 46,669.

The 142 wpm figure agrees with the conventional 130–150 wpm range for conversational speech,
which is mild evidence the sample is not anomalous.

**Against the current 100 KiB cap:**

- Cleaned text: a 60-minute call uses **~45%** of the cap. Even a 2-hour call fits.
- Raw Teams VTT: a 60-minute call is **~130%** of the cap — rejected. A 45-minute call is
  ~97% — accepted by luck.
- Raw VTT crosses 100 KiB at about **46 minutes**.

The cap is therefore *already* in the wrong place for the naive path and *comfortable* for the
clean path, which is the substance of finding 2 above.

---

## 6. Uncertainty markers: there are none

- **`[inaudible]` and `[crosstalk]` are human conventions.** Rev's transcription style guide
  defines `[inaudible hh:mm:ss]` and `[crosstalk hh:mm:ss]` as tags a *human transcriber*
  applies. Neither appears in any machine-generated file measured here.
- **No confidence anywhere in the pipeline the caller sees.** WebVTT has no confidence
  construct. Whisper's `verbose_json` carries per-segment `avg_logprob`/`no_speech_prob`, but
  nothing that survives into a `.vtt`/`.srt`/Doc export.
- **The real failure mode is fabrication, not omission.** The FAccT'24 "Careless Whisper" study
  found hallucinated sequences in ~1.4% of audio segments, disproportionately triggered by
  silence and disfluency, ~40% of them harmful. OpenAI has since mitigated much of it. Zoom's
  own documentation notes its ASR does not produce punctuation or capitalisation at all.

**Implication for #53:** there is no marker for extraction to read as uncertainty, so it must
not be told to look for one. The live risk runs the other way — a fluent hallucinated sentence
is indistinguishable from a real statement, which argues that **`kind: transcript` should make
extraction *more* willing to record `unknown`/`Assumption`, not less** (`CONTEXT.md`: *Unknown
= not stated; Assumption = inferred on the record*). A transcript is a weaker evidentiary
substrate than a written architecture description, and the guidance should say so.

**Implication for #56:** a `source_excerpt` quoted from a transcript may quote a hallucination
verbatim. That is not a reason to drop excerpts — traceability to *what the service was told*
is exactly the point — but it does mean the excerpt is evidence about the input, not about the
system, and the report should not imply otherwise.

---

## 7. Limits of this research

- **The measured corpus is webinars, not interviews.** All four Teams files are Azure user-group
  sessions with a dominant presenter (hence the 1,667-word turn). The *rates* — wpm, bytes/min,
  clean fraction, cue fragmentation, attribution coverage — are properties of the tooling and
  transfer to any Teams meeting. The *turn-length distribution* does not; a genuine 1:1
  analyst↔developer interview will be far more balanced and slightly denser in words per minute.
- **Zoom is one real file plus documentation**, not a measured corpus. The shape is corroborated
  by a second, synthetic-looking fixture and by Zoom's docs, but no Zoom byte-density figure is
  claimed here.
- **Nothing was measured for Granola/Fathom/Otter** beyond their documented export options;
  those rows in §1 are documentation, not measurement.
- **No `.docx` was inspected.** Teams and Meet both offer it and both are described as carrying
  speaker names; the byte question is moot since the front-end decodes to text regardless.

---

## Sources

Measured artifacts (public repos):

- [nicolasblank/AZMGZA](https://github.com/nicolasblank/AZMGZA) — four real Teams transcript
  `.vtt` exports, October 2025.
- [Ao-Uegaki/client-survey-notes](https://github.com/Ao-Uegaki/client-survey-notes) — a real
  Zoom `.vtt` (`GMT20260428-065744_Recording.transcript.vtt`), cleaned, cue ids and timestamps
  preserved.
- [Bensmina-Anass/meeting-archaeologist](https://github.com/Bensmina-Anass/meeting-archaeologist),
  [luizcarlosdk/Memoir](https://github.com/luizcarlosdk/Memoir) — fixtures illustrating `NOTE`
  attendee blocks and the Zoom `Name:` payload shape.

Documentation:

- [W3C WebVTT: The Web Video Text Tracks Format](https://www.w3.org/TR/webvtt1/)
- [Start, stop, and download live transcripts in Microsoft Teams meetings](https://support.microsoft.com/en-us/teams/meetings/start-stop-and-download-live-transcripts-in-microsoft-teams-meetings)
- [Use Microsoft Teams Rooms to identify in-room participants in a meeting transcription](https://support.microsoft.com/en-us/teams/calls-devices/use-microsoft-teams-intelligent-speakers-to-identify-in-room-participants-in-a-meeting-transcription)
- [Tenant administration control for voice recognition in Teams Rooms](https://learn.microsoft.com/en-us/microsoftteams/rooms/voice-recognition)
- [Using audio transcription for cloud recordings (Zoom)](https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0064927)
- [Export conversations (Otter.ai help centre)](https://help.otter.ai/hc/en-us/articles/360047733634-Export-conversations)
- [wassimk/granary — Granola notes and transcripts to Markdown](https://github.com/wassimk/granary)
- [Rev Transcription Style Guide v4.0.1](https://cf-public.rev.com/styleguide/transcription/Rev+Transcription+Style+Guide+v4.0.1.pdf)
- [Koenecke et al., *Careless Whisper: Speech-to-Text Hallucination Harms*, FAccT '24](https://facctconference.org/static/papers24/facct24-111.pdf)
- [AI transcription tools 'hallucinate,' too — *Science*](https://www.science.org/content/article/ai-transcription-tools-hallucinate-too)
