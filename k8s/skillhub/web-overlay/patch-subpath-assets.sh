#!/bin/sh
set -eu

root="${1:-/usr/share/nginx/html}"
index="${root}/index.html"
fonts="${root}/fonts/fonts.css"
base="${SKILLHUB_WEB_BASE_PATH:-/platform/skillhub}"
base="/${base#/}"
base="${base%/}"
placeholder="/__SKILLHUB_WEB_BASE_PATH__"

test -s "${index}"
test -s "${fonts}"

# frame-ancestors is ignored in a meta policy. Embedding policy belongs to the
# HTTP response header owned by the reverse proxy, not to a static HTML tag.
sed -i "s/frame-ancestors 'none';[[:space:]]*//g" "${index}"

# SkillHub is served below /platform/skillhub/. Root-relative references escape
# that prefix and hit the workspace frontend, so keep static references local to
# the document/CSS URL instead.
sed -i \
  -e "s#href=\"${placeholder}/favicon\\.svg\"#href=\"${base}/favicon.svg\"#g" \
  -e "s#href=\"${placeholder}/fonts/fonts\\.css\"#href=\"${base}/fonts/fonts.css\"#g" \
  -e "s#href=\"/favicon\\.svg\"#href=\"${base}/favicon.svg\"#g" \
  -e "s#href=\"/fonts/fonts\\.css\"#href=\"${base}/fonts/fonts.css\"#g" \
  "${index}"
sed -i \
  -e "s#url('/fonts/#url('./#g" \
  -e 's#url("/fonts/#url("./#g' \
  "${fonts}"

if grep -Fq "frame-ancestors 'none'" "${index}"; then
  echo "invalid_meta_frame_ancestors_still_present=${index}" >&2
  exit 1
fi
grep -Fq "href=\"${base}/favicon.svg\"" "${index}"
grep -Fq "href=\"${base}/fonts/fonts.css\"" "${index}"
if grep -Eq "url\\((['\"]?)/fonts/" "${fonts}"; then
  echo "root_relative_font_url_still_present=${fonts}" >&2
  exit 1
fi

for file in \
  inter-latin-400-normal.woff2 \
  inter-latin-500-normal.woff2 \
  inter-latin-600-normal.woff2 \
  inter-latin-700-normal.woff2 \
  jetbrains-mono-latin-400-normal.woff2 \
  jetbrains-mono-latin-500-normal.woff2
do
  test -s "${root}/fonts/${file}"
done
