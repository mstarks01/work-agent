#!/usr/bin/env bash
# The counting rule of design.md, over each run file of one arm.
# Usage: count.sh <runs folder> <prefix>
set -eu
for file in "$1"/"$2"-*.json; do
  jq -c --arg file "$(basename "$file")" '{
    file: $file,
    read: (.schema_error == null),
    repaired: (.schema_error == null and (.remaining | length) == 0 and (.unbriefed | length) == 0),
    kept_fault: [.remaining[] | select(.message as $m | $m | IN($ARGS.named.p[]))] | length,
    new_problems: [.remaining[] as $r | select(.problems | index($r.message) | not) | $r] | length,
    unbriefed: (.unbriefed | length),
    status: "\(.status.answered) -> \(.status.repaired)"
  }' --argjson p "$(jq -c .problems "$file")" "$file"
done | jq -s '{views: ., repaired: map(select(.repaired)) | length, total: length,
  new_problems: map(.new_problems) | add, unbriefed: map(.unbriefed) | add,
  unread: map(select(.read | not)) | length,
  status_changed: map(select(.repaired and (.status | test("^(\\S+) -> \\1$") | not))) | length}'
