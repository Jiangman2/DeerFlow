# Unified search TLS compatibility

The 2026-10-30 production diagnostics found intermittent certificate verification failures through the enterprise HTTP proxy. OpenSSL code 66 (end-entity key too small) was observed at security level 2. Level 1 improved transport connectivity on some sampled nodes; code 20 (unable to obtain the issuer certificate locally) remained on another node. Unauthenticated HTTP 401 receipts establish transport connectivity only, not successful search.

## Operator configuration

Keep the existing SSL policy unless compatibility is explicitly needed. Set these Gateway environment variables for the scoped workaround:

```yaml
- name: DEER_FLOW_WEB_ROUTER_TRUST_ENV
  value: "true"
- name: DEER_FLOW_WEB_ROUTER_TLS_SECURITY_LEVEL
  value: "1"
```

Also retain the deployment's approved proxy variables and mounted enterprise CA bundle. The client selects the first nonempty `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, or `CURL_CA_BUNDLE`. Do not commit proxy credentials or API keys. Level 1 requires trust-env enabled and an explicit CA file. Invalid levels and unreadable CA files fail closed. The switch accepts only `1` and `2`; unset/`2` leaves the original SSL-context policy unchanged (observed as level 2 in the production image).

Only fixed-host unified search provider clients use `DEFAULT:@SECLEVEL=1`. CA-chain verification, hostname checks, and disabled redirects remain in place. Model clients, internal relays, Work, and Runner retain independent policies. This compatibility option permits weaker cryptography and is temporary; fix the proxy/certificate chain and return to the default policy when the network supports it. It does not resolve code 20.

## Source and temporary production overlay

This source contains the same `providers.py` bytes as the temporary hotfix, SHA256 `f0ac1b0289bb68f6a5999853459ab86d386ccb053aacffa799df9f8a06cf5193`.

The hotfix package can mount ConfigMap `deer-flow-search-tls-compat-f0ac1b0289bb` through volume `search-tls-compat-f0ac1b0289bb` onto `/app/backend/packages/harness/deerflow/community/web_router/providers.py`. Source synchronization does not establish that this hotfix has actually been deployed. Inspect the live Deployment and its receipts.

An active subPath mount overrides the image file even after an image rebuild. For the next release:

1. Build a new Gateway image from the commit containing this change; record commit and image digest. Existing image QA receipts do not qualify a rebuilt image.
2. Test the installed image without the code overlay, using the intended CA/proxy settings. Verify policy and perform authenticated search/HTML acceptance; PDF acceptance remains separately deferred.
3. Preserve the current Deployment template and rollback image/configuration binding. If the overlay is present, switch image and remove only its exact volumeMount and volume in one guarded Pod-template update. Retain the compatibility environment variables while required. Do not delete the enterprise CA mount or application configuration.
4. Roll Gateway using the existing availability strategy and verify each new Pod's image, installed module hash, effective SSL context, health, and real user search flow. Do not delete the old overlay ConfigMap while it is still needed for rollback.
5. Keep database/schema rollback separate; this TLS change does not access or migrate the database.

## Tests and release records

`backend/tests/test_web_router_tls_security_level.py` exercises the production policy function with real SSL contexts, isolated from the application import graph. Run it alongside the existing unified-router and enterprise transport suites. Source tests use synthetic credentials and are not production provider acceptance.

Update `deploy/enterprise/work-source-candidate-20261026.json` to bind the new provider hash. Preserve historical production source locks and earlier image receipts. The TLS source sync tool creates a separate Git worktree, runs the related 243 tests, commits only the scoped source/tests/docs/candidate-manifest changes, and optionally performs a normal non-force push to `codex/enterprise-consolidation-20260716`.
