#!/bin/sh
out=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--json-output" ]; then
    out="$arg"
  fi
  prev="$arg"
done
printf '[]' > "$out"
exit 0
