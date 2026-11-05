#!/bin/sh
# Runs only after the existing Office preview init container, on its emptyDir.
set -eu
root="${1:-/patched-ui}"
payload="${2:-/opt/weknora-member-fix}"
test -d "$root/assets" && test ! -L "$root" && test ! -L "$root/assets"
test ! -e "$root/weknora-members-fix-r1.json"
test -z "$(find "$root" -type l -print -quit)"
cd "$root"
sha256sum -c "$payload/baseline.sha256"
cd "$payload"
sha256sum -c payload.sha256
cd "$root"
count=0
for old in assets/*.legacy-office-r6.js; do
    test -f "$old"
    count=$((count + 1))
done
test "$count" -ge 40 && test "$count" -le 250
# Never modify an existing hashed module. Keep the old graph for open tabs.
for old in assets/*.legacy-office-r6.js; do
    new="${old%.js}.members-r1.js"
    test ! -e "$new"
    input="$old"
    case "$old" in
        assets/OrganizationList-A-11TnnH.legacy-office-r6.js|assets/settings-CaJr93kK.legacy-office-r6.js)
            input="$payload/${old##*/}" ;;
    esac
    sed 's/\.legacy-office-r6\.js/.legacy-office-r6.members-r1.js/g' "$input" > "$new"
    chmod 0644 "$new"
done
test ! -e index.members-r1.html
sed 's/\.legacy-office-r6\.js/.legacy-office-r6.members-r1.js/g' index.html > index.members-r1.html
! grep -q '\.legacy-office-r6\.js' index.members-r1.html
grep -q 'main-DIVOhrmF.legacy-office-r6.members-r1.js' index.members-r1.html
grep -q '__wkMemberSlice' assets/OrganizationList-A-11TnnH.legacy-office-r6.members-r1.js
grep -q '__wkMemberRequest' assets/settings-CaJr93kK.legacy-office-r6.members-r1.js
chmod 0644 index.members-r1.html
mv index.members-r1.html index.html
printf '{"status":"MEMBER_UI_PATCH_INSTALLED","version":"members-r1","module_count":%s}\n' "$count" > weknora-members-fix-r1.json
chmod 0644 weknora-members-fix-r1.json
printf 'MEMBER_UI_PATCH_INSTALLED modules=%s\n' "$count"
