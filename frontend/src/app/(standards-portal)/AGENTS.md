## SeaStandard local portal

`/workspace/standards` belongs to the independent `(standards-portal)` route group.
Keep its server authentication guard and full-width, scrollable layout separate
from the chat sidebar shell. Its CSS Module consumes the shared CNOOC tokens;
copy lives in `core/i18n/standards-portal.ts`. All seven entries are interactive.
Read `STANDARDS_PORTAL_AGENTS_JSON` on the server at request time through
`core/standards-portal/config.ts`: fixed module keys map to raw employee internal
names, encoded once into same-origin native `chats/new` routes. Missing or invalid
entries open a localized configuration notice; never substitute generic chat or
an example employee. Keep same-tab navigation, existing auth and native employee
permissions. Do not add return navigation, gallery hiding/sorting, or a default
portal employee. See `docs/standards-portal-configuration.md` at repository root.
