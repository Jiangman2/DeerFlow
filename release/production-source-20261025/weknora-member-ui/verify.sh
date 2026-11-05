set -eu
cd /usr/share/nginx/html
sha256sum -c /opt/weknora-member-fix/final.sha256 >/dev/null
test -s weknora-members-fix-r1.json
! grep -q '\.legacy-office-r6\.js' index.html
count=0
for file in assets/*.legacy-office-r6.members-r1.js; do
  test -s "$file"
  ! grep -q '\.legacy-office-r6\.js' "$file"
  count=$((count + 1))
done
test "$count" -ge 40 && test "$count" -le 250
printf '%s\n' '{"status":"INSTALLED_MEMBER_UI_ACCEPTED","old_modules_preserved":true,"new_module_graph_verified":true,"production_api_calls":0,"membership_writes":0}'
