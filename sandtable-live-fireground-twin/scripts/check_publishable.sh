#!/usr/bin/env bash
# Lists publishable files (tracked, or untracked and not ignored) that contain a build-machine path, the local user name,
# a tailnet-range address or something that looks like a key. Exit code 1 if any. Run before committing.
cd "$(dirname "$0")/.."
U=$(id -un); H=$(hostname)
{ git ls-files; git ls-files --others --exclude-standard; } | sort -u | tr '\n' '\0' |
  xargs -0 grep -I -l -E "$U|/home/[a-z]|/Users/[A-Za-z]|$H|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[0-9]+|sk-ant-|API_KEY=[A-Za-z0-9_-]{12,}" 2>/dev/null |
  grep -v "^scripts/check_publishable.sh$" > /tmp/.sandtable_pub_offenders.$$ || true
if [ -s /tmp/.sandtable_pub_offenders.$$ ]; then echo "NOT publishable:"; cat /tmp/.sandtable_pub_offenders.$$; rm -f /tmp/.sandtable_pub_offenders.$$; exit 1; fi
rm -f /tmp/.sandtable_pub_offenders.$$; echo "publishable: no machine paths, user names, tailnet addresses or keys found"
