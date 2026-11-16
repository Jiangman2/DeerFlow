# Authenticated daily HTML reader and Skill 2.1.5

The operator's November 14 22:25 read-only preflight established Nginx 116,
Gateway 417 and Frontend 244. The accepted Nginx source is the immutable
`deer-flow-nginx-hostguard-20261011-r1` ConfigMap, UID
`e9d12df6-1f22-4a2a-818e-02bdb9b4efd9`, with `nginx.conf` SHA256
`f8a07eb8e8b8bc86d74cb5666c0cd4917505da5cd9a1eaa370c906c91daf0d95`.
The release reads and checks those exact live inputs again before mutation.

This release adds `/reports/energy-daily/<thread_id>/<publication_date>.html`.
Nginx internally requests the existing conversation artifact endpoint with the
browser session cookie. Existing Gateway permission and trusted-owner storage
resolution remain authoritative. A 401 redirects through IAM with a relative
`next`; 403 and 404 return readable failure pages. There is no public sharing
token or anonymous file access.

Only this narrow route changes the existing HTML download response to inline
HTML, with a scriptless CSP sandbox and no network access. The regular artifact
download route is preserved. Client-supplied bearer/internal owner headers are
cleared. The filename is fixed to the Skill's Chinese output basename and date.
Nginx escapes the rewritten UTF-8 filename once; percent-encoding it in the
configuration caused a double-encoding defect caught by the real HTTP tests.

The Skill writes the webpage link and the existing conversation link into the
same complete TXT during `build`. A single successful `present_files` handoff
remains required before replying or an explicitly authorized external send.
There is no extra model call, link lookup, OCR, publication transcription or
second supplementary reply. Five core acquisition/calculation/render/runtime
modules are byte-identical to accepted 2.1.4. Generic environments without host
conversation context do not invent a link.

The combined installer first tests the candidate Nginx configuration in an
owned ready Nginx Pod using a private temporary file, creates an immutable
ConfigMap clone, then CAS-patches only its name reference in the Deployment.
All other Deployment fields and the original ConfigMap remain unchanged.
Nginx must finish its rollout and return the exact anonymous IAM redirect
before the Skill installer runs. It then reuses the existing browser runtime,
qualifies the new Skill on ARM64 as UID 1000 with the public-only mount, and
publishes it using the previously accepted Skill-only workflow. It requires
the exact 2.1.4 install receipt and tree. No Gateway rollout, provider request,
credential modification, message, schedule creation or run replay is requested.

Local checks: 34 Skill tests on Windows and Linux/Python 3.10/UID 1000; eight
configuration parser tests; five activation/CAS/concurrency tests; 15 real
Nginx HTTP checks for each upstream form; mobile 390px HTML/SVG rendering with
the actual isolated Nginx response and blocked inline script. BrowserAct had
no configured browser or API key, so the mobile test used existing local
Playwright. These are local/synthetic checks, not production user acceptance.

Production command, once only, after transferring the sealed combined script:

```bash
sudo python3 -I -B -u /home/cnooc/install-energy-daily-html-links-20261114-r1.py --install-daily-html-links-once
```

Keep the final JSON on failure; do not replay it or remove markers. The
combined file also supports `--verify-reader-readonly` for the reader's saved
plan and live state; that mode never publishes the Skill. It may save a new
local verification receipt. The original installation and source inventories
are immutable. Real IAM return navigation, owner/admin permissions and Work
mobile clicks remain business acceptance after deployment.
