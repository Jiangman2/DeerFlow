set -eu
original_path_set=${PATH+x}
original_path=${PATH-}
original_locale_set=${LC_ALL+x}
original_locale=${LC_ALL-}
PATH=/usr/bin:/bin
LC_ALL=C
export PATH LC_ALL
fail() { printf '%s\n' 'OWNED_POD_LINK_PREEXEC_REFUSED' >&2; exit 91; }
[ "$#" -ge 3 ] || fail
owner=$1
code_sha=$2
shift 2
receipt=/portal-ppt-link-guard
for tool in cat readlink stat; do command -v "$tool" >/dev/null 2>&1 || fail; done
[ -n "${PPT_GUARD_POD_UID:-}" ] || fail
[ "$(cat "$receipt/READY")" = COMPLETE ] || fail
[ "$(cat "$receipt/owner")" = "$owner" ] || fail
[ "$(cat "$receipt/pod_uid")" = "$PPT_GUARD_POD_UID" ] || fail
[ "$(cat "$receipt/code_sha256")" = "$code_sha" ] || fail
expected_ns=$(cat "$receipt/netns_stat") || fail
expected_link=$(cat "$receipt/netns_link") || fail
expected_interfaces=$(cat "$receipt/interfaces") || fail
check_ns() {
  [ "$(stat -Lc '%d:%i' /proc/self/ns/net)" = "$expected_ns" ] || fail
  [ "$(readlink /proc/self/ns/net)" = "$expected_link" ] || fail
}
check_caps() {
  seen=0
  while IFS=: read -r key value; do
    case "$key" in
      CapEff|CapPrm|CapBnd|CapAmb|CapInh)
        set -- $value
        [ "$#" -eq 1 ] || fail
        case "$1" in ''|*[!0]*) fail;; esac
        seen=$((seen + 1));;
      NoNewPrivs)
        set -- $value
        [ "$#" -eq 1 ] && [ "$1" = 1 ] || fail
        seen=$((seen + 1));;
    esac
  done < /proc/self/status
  [ "$seen" -eq 6 ] || fail
}
snapshot() {
  lo_count=0
  count=0
  for path in /sys/class/net/*; do
    [ -e "$path/ifindex" ] && [ -e "$path/flags" ] || fail
    name=${path##*/}
    case "$name" in ''|*[!A-Za-z0-9_.:-]*) fail;; esac
    index=$(cat "$path/ifindex") || fail
    case "$index" in ''|*[!0-9]*|0) fail;; esac
    flags=$(cat "$path/flags") || fail
    case "$flags" in 0x*) hex=${flags#0x};; *) fail;; esac
    case "$hex" in ''|*[!0-9a-fA-F]*) fail;; esac
    bits=$((flags))
    if [ "$name" = lo ]; then
      [ "$((bits & 1))" -eq 1 ] && [ "$((bits & 8))" -eq 8 ] || fail
      lo_count=$((lo_count + 1))
    else
      [ "$((bits & 1))" -eq 0 ] && [ "$((bits & 8))" -eq 0 ] || fail
    fi
    count=$((count + 1))
    [ "$count" -le 64 ] || fail
    printf '%s %s\n' "$index" "$name"
  done
  [ "$lo_count" -eq 1 ] || fail
}
check_ns
check_caps
actual=$(snapshot) || fail
[ "$actual" = "$expected_interfaces" ] || fail
check_ns
check_caps
actual=$(snapshot) || fail
[ "$actual" = "$expected_interfaces" ] || fail
check_ns
if [ "$original_path_set" = x ]; then PATH=$original_path; export PATH; else unset PATH; fi
if [ "$original_locale_set" = x ]; then LC_ALL=$original_locale; export LC_ALL; else unset LC_ALL; fi
exec "$@"
