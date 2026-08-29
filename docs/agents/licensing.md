# Licensing

What you may copy into this repo, and what you must record when you do.

## The split

Apache-2.0 covers the code. It does not cover everything in the tree.

`src/analysis_service/frameworks/asvs/catalog.json` and the 17 files at
`frameworks/asvs/lanes/*/skill.md` reproduce the 345 requirement sentences of
OWASP ASVS 5.0.0. OWASP publishes ASVS under CC BY-SA 4.0. Those 18 files carry
CC BY-SA 4.0 and its ShareAlike condition.

`NOTICE` is the authoritative list. `THIRD_PARTY` in
`tests/test_license_lints.py` is the same fact in a form the lints read.

## The rule that reaches you

**Never copy a sentence out of a governed file into a file that is not
governed.** Write the point in your own words instead.

This is the failure that costs something, and it is easy to make. A requirement
sentence reads like ordinary prompt text. Pasting one into a new lane, a test
fixture, an exemplar, a docstring or a doc puts ShareAlike text inside an
Apache-2.0 file, and the distribution stops matching what `NOTICE` says it is.

`test_no_upstream_sentence_appears_in_an_ungoverned_file` catches this. It
fingerprints every fifteen-word run of the upstream sentences and looks for those
words anywhere in the tree. Reformatting does not hide a passage from it: case,
punctuation and markup are all stripped before the comparison.

**Citing a standard is always fine.** STRIDE's lanes point at OWASP identifiers
such as `A01` and `ASI08` and carry no obligation, because a short identifier is
not the expression it points at. The question the lint asks is whether a reader
receives the upstream author's words, never whether the upstream project is
mentioned.

## When you add a framework package

A package that quotes a published standard inherits that standard's licence.

1. Add the package's SPDX identifier to `CONTENT_LICENSE` in
   `src/analysis_service/frameworks/__init__.py`. A missing key fails
   `tests/test_framework_neutrality.py`.
2. If it is not `Apache-2.0`, add a `THIRD_PARTY` entry naming the upstream
   licence, the files it governs, and a reader for the upstream text.
3. Write the matching `NOTICE` section. The lints fail until it names the work,
   its licence, and every governed path.

State the reason as a property of the framework, never as its name — see
[framework-parity.md](framework-parity.md).

## When you add a corpus case

A case converted from somebody else's model records its source **and its
licence** in the `provenance` field of its `case.json`. A case that names an
upstream URL and no licence fails
`test_every_corpus_case_from_an_external_source_names_its_license`.

Attribution-only licences (CC BY 4.0, MIT, Apache-2.0) permit the conversion to
carry the repo licence, so the case stays Apache-2.0 and `NOTICE` credits the
source. A ShareAlike source does not permit that, and the converted case would
have to carry the upstream licence instead.

## When you add a dependency

`test_no_locked_dependency_is_copyleft` reads `uv.lock` and refuses AGPL, GPL,
LGPL, SSPL, EUPL, OSL, CPAL and CDDL. MPL-2.0 passes, because its copyleft is
per-file and an unmodified file travels inside a larger work under other terms.

A development tool is run rather than shipped, so its licence puts no condition
on the wheel. The check cannot see which dependency group a distribution arrived
in, so declare that case in `DECLARED_COPYLEFT` with the reason.

## What the lints do not decide

Whether the code licence stays Apache-2.0. That is open by decision and gets
revisited at 1.0. There is no contributor licence agreement, so relicensing
depends on the copyright staying single-holder.
