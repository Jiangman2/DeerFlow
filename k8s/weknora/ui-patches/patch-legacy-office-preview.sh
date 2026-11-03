#!/bin/sh

set -eu

SOURCE_ROOT="${1:-/usr/share/nginx/html}"
TARGET_ROOT="${2:-/patched-ui}"
CACHE_SUFFIX='.legacy-office-r6'
ASSET_ROOT="${TARGET_ROOT}/assets"

test -d "${SOURCE_ROOT}"
test -d "${TARGET_ROOT}"
cp -a "${SOURCE_ROOT}/." "${TARGET_ROOT}/"

count_literal() {
  needle="$1"
  total=0
  while IFS= read -r file; do
    count="$(grep -oF "${needle}" "${file}" 2>/dev/null | wc -l | tr -d ' ')"
    total=$((total + count))
  done <<EOF
$(find "${TARGET_ROOT}" -type f -name '*.js' -print)
EOF
  printf '%s\n' "${total}"
}

patch_registrations() {
  while IFS= read -r file; do
    if grep -qF "${DOC_OLD_DOUBLE}" "${file}" ||
       grep -qF "${DOC_OLD_SINGLE}" "${file}" ||
       grep -qF "${PPT_OLD_DOUBLE}" "${file}" ||
       grep -qF "${PPT_OLD_SINGLE}" "${file}"; then
      sed -i \
        -e 's/"docx",\["docx","doc"\]/"docx",["docx"]/g' \
        -e "s/'docx',\['docx','doc'\]/'docx',['docx']/g" \
        -e 's/"pptx",\["pptx","ppt"\]/"pptx",["pptx"]/g' \
        -e "s/'pptx',\['pptx','ppt'\]/'pptx',['pptx']/g" \
        "${file}"
    fi
  done <<EOF
$(find "${TARGET_ROOT}" -type f -name '*.js' -print)
EOF
}

cache_bust_javascript_graph() {
  manifest="${TARGET_ROOT}/.weknora-javascript-assets"
  : >"${manifest}"
  test -d "${ASSET_ROOT}"
  # Only Vite's /assets module graph is safe to rename.  Fixed public-root
  # scripts such as /config.js and /deerflow-enterprise-bootstrap.js are
  # routed by exact production Nginx locations and must retain their names.
  find "${ASSET_ROOT}" -type f -name '*.js' -print >"${manifest}"
  javascript_assets="$(wc -l <"${manifest}" | tr -d ' ')"
  test "${javascript_assets}" -ge 1

  while IFS= read -r old_file; do
    new_file="${old_file%.js}${CACHE_SUFFIX}.js"
    cp -p "${old_file}" "${new_file}"
  done <"${manifest}"

  while IFS= read -r old_file; do
    old_name="${old_file##*/}"
    new_name="${old_name%.js}${CACHE_SUFFIX}.js"
    while IFS= read -r graph_file; do
      if grep -qF "${old_name}" "${graph_file}" 2>/dev/null; then
        sed -i "s|${old_name}|${new_name}|g" "${graph_file}"
      fi
    done <<EOF
$(find "${TARGET_ROOT}" -type f \( -name '*.html' -o -name "*${CACHE_SUFFIX}.js" -o -name '*.json' -o -name '*.css' \) -print)
EOF
  done <"${manifest}"

  versioned_assets="$(find "${TARGET_ROOT}" -type f -name "*${CACHE_SUFFIX}.js" -print | wc -l | tr -d ' ')"
  test "${versioned_assets}" -eq "${javascript_assets}"
  html_references=0
  while IFS= read -r html_file; do
    if grep -qF "${CACHE_SUFFIX}.js" "${html_file}" 2>/dev/null; then
      html_references=$((html_references + 1))
    fi
  done <<EOF
$(find "${TARGET_ROOT}" -type f -name '*.html' -print)
EOF
  test "${html_references}" -ge 1
  rm -f "${manifest}"
  printf '%s\n' "${versioned_assets}"
}

DOC_OLD_DOUBLE='"docx",["docx","doc"]'
DOC_NEW_DOUBLE='"docx",["docx"]'
DOC_OLD_SINGLE="'docx',['docx','doc']"
DOC_NEW_SINGLE="'docx',['docx']"
PPT_OLD_DOUBLE='"pptx",["pptx","ppt"]'
PPT_NEW_DOUBLE='"pptx",["pptx"]'
PPT_OLD_SINGLE="'pptx',['pptx','ppt']"
PPT_NEW_SINGLE="'pptx',['pptx']"

doc_old=$(( $(count_literal "${DOC_OLD_DOUBLE}") + $(count_literal "${DOC_OLD_SINGLE}") ))
ppt_old=$(( $(count_literal "${PPT_OLD_DOUBLE}") + $(count_literal "${PPT_OLD_SINGLE}") ))
if [ "${doc_old}" -gt 0 ] || [ "${ppt_old}" -gt 0 ]; then
  patch_registrations
fi

doc_old_after=$(( $(count_literal "${DOC_OLD_DOUBLE}") + $(count_literal "${DOC_OLD_SINGLE}") ))
ppt_old_after=$(( $(count_literal "${PPT_OLD_DOUBLE}") + $(count_literal "${PPT_OLD_SINGLE}") ))
doc_new=$(( $(count_literal "${DOC_NEW_DOUBLE}") + $(count_literal "${DOC_NEW_SINGLE}") ))
ppt_new=$(( $(count_literal "${PPT_NEW_DOUBLE}") + $(count_literal "${PPT_NEW_SINGLE}") ))
test "${doc_old_after}" -eq 0
test "${ppt_old_after}" -eq 0
test "${doc_new}" -ge 1
test "${ppt_new}" -ge 1

versioned_assets="$(cache_bust_javascript_graph)"
test "${versioned_assets}" -ge 1
test ! -e "${TARGET_ROOT}/config${CACHE_SUFFIX}.js"
test ! -e "${TARGET_ROOT}/deerflow-enterprise-bootstrap${CACHE_SUFFIX}.js"

versioned_doc_old=0
versioned_ppt_old=0
versioned_doc_new=0
versioned_ppt_new=0
while IFS= read -r file; do
  count="$(grep -oF "${DOC_OLD_DOUBLE}" "${file}" 2>/dev/null | wc -l | tr -d ' ')"
  versioned_doc_old=$((versioned_doc_old + count))
  count="$(grep -oF "${DOC_OLD_SINGLE}" "${file}" 2>/dev/null | wc -l | tr -d ' ')"
  versioned_doc_old=$((versioned_doc_old + count))
  count="$(grep -oF "${PPT_OLD_DOUBLE}" "${file}" 2>/dev/null | wc -l | tr -d ' ')"
  versioned_ppt_old=$((versioned_ppt_old + count))
  count="$(grep -oF "${PPT_OLD_SINGLE}" "${file}" 2>/dev/null | wc -l | tr -d ' ')"
  versioned_ppt_old=$((versioned_ppt_old + count))
  count="$(grep -oF "${DOC_NEW_DOUBLE}" "${file}" 2>/dev/null | wc -l | tr -d ' ')"
  versioned_doc_new=$((versioned_doc_new + count))
  count="$(grep -oF "${DOC_NEW_SINGLE}" "${file}" 2>/dev/null | wc -l | tr -d ' ')"
  versioned_doc_new=$((versioned_doc_new + count))
  count="$(grep -oF "${PPT_NEW_DOUBLE}" "${file}" 2>/dev/null | wc -l | tr -d ' ')"
  versioned_ppt_new=$((versioned_ppt_new + count))
  count="$(grep -oF "${PPT_NEW_SINGLE}" "${file}" 2>/dev/null | wc -l | tr -d ' ')"
  versioned_ppt_new=$((versioned_ppt_new + count))
done <<EOF
$(find "${TARGET_ROOT}" -type f -name "*${CACHE_SUFFIX}.js" -print)
EOF

test "${versioned_doc_old}" -eq 0
test "${versioned_ppt_old}" -eq 0
test "${versioned_doc_new}" -ge 1
test "${versioned_ppt_new}" -ge 1

if [ "${doc_old}" -gt 0 ] || [ "${ppt_old}" -gt 0 ]; then
  echo "weknora_legacy_office_preview_patch=PASS mode=patched cache_bust=versioned-js-graph versioned_assets=${versioned_assets} doc_registrations=${doc_old} ppt_registrations=${ppt_old}"
else
  echo "weknora_legacy_office_preview_patch=PASS mode=already-compatible cache_bust=versioned-js-graph versioned_assets=${versioned_assets}"
fi
