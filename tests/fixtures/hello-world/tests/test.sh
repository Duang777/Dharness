#!/bin/sh

set -eu
mkdir -p /logs/verifier

if [ -f /app/hello.txt ] &&
    [ "$(cat /app/hello.txt)" = "Hello, world!" ] &&
    [ "$(wc -l < /app/hello.txt)" -eq 1 ] &&
    [ ! -e /app/verification-only.txt ]; then
  printf '1\n' > /logs/verifier/reward.txt
  exit 0
fi

printf '0\n' > /logs/verifier/reward.txt
exit 1
