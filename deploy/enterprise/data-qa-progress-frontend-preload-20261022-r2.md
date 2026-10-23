# Data QA progress frontend preload, 2026-10-22 r2

This package only preloads the previously qualified ARM64 frontend image. It does not change a Deployment, restart a Pod, execute the image, read credentials, or access the application database. Frontend activation remains a separate step after both preload receipts and fresh Kubernetes node checks.

Package directory: `C:/Users/15285/Downloads/portal-data-qa-progress-20261022-r2`.

The unchanged candidate manifest is `sha256:15d1327740e688faf0617d1d8fb9f662618a963624ba7280a6d0efb54ed70475`; its config is `sha256:377bac0b69157c9cd0b1514ca986343381a6541ef30135127df4876f350d5ffc`. All 11 layers, blob hashes, and uncompressed diff IDs were verified during packaging. The archive is a verified copy of the existing r1 full OCI.

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `frontend.data-qa-progress.full.oci.tar` | 127319040 | `aeb791af15e7456ad41c1d64590e5f9796071475c5066da6bb46922418eae7b2` |
| `preload-portal-frontend-data-qa-progress-0015-20261022-r2.py` | 85305 | `7e6ddc2fc22b905f296888b0aea61ad8a3f73d67dd0323079489846cac61596c` |
| `preload-portal-frontend-data-qa-progress-datax02-20261022-r2.py` | 86890 | `1b478db9eb36910b1a5fbb95a4b523539cbafecc64c9079393ee116adbfc7707` |

Each script now accepts only its named host. The node identity pins are:

- `ecs-zdz-pro-0015`: `de6ee5e8-dbfb-4272-9ebf-ae82182db085`.
- `datax-02`: `3148d4c1-6a97-42ed-a78c-950dcea5cbec`.

These are historical identity pins; the preload deliberately does not claim a fresh Kubernetes UID check. Activation must check both UIDs and the current frontend binding: UID `bb0b3f6e-31f7-4073-8b76-7650ea5b96ee`, generation `225`, spec SHA256 `1d3af5d2091bbb29fb4a45a3743acc03fbff9bf190c23c67b5bc43bb4fc37123`, current image `sha256:ee145e6357e4f6b63003d786b7c6b0f28cf6bbe663e1b5589f92f7c829bb89e5`.

Copy the archive and the matching host script into `/home/cnooc/`. Verify their SHA256 values, then run exactly the matching command on that host as root:

```sh
# ecs-zdz-pro-0015 only
sudo python3 -I -B /home/cnooc/preload-portal-frontend-data-qa-progress-0015-20261022-r2.py --preload-frontend-data-qa-progress-once
```

```sh
# datax-02 only
sudo python3 -I -B /home/cnooc/preload-portal-frontend-data-qa-progress-datax02-20261022-r2.py --preload-frontend-data-qa-progress-once
```

Before creating its control directory, each script checks the existing once marker, runs bounded read-only containerd/CRI metadata queries, applies the unchanged cache-aware space and inode calculation, and verifies the input archive hash. The control parent supplies the future directory's filesystem identity. The original nodefs reserve of 10% plus 256 MiB and imagefs reserve of 16% plus 3 GiB remain in place. Native archive sealing, validation, import, unpack, reference preservation, CRI checks, and later capacity rechecks are unchanged.

The control directories are `/mnt/data/portal-frontend-data-qa-progress-preload-20261022-r2` on 0015 and `/var/lib/portal-frontend-data-qa-progress-preload-20261022-r2` on datax-02. Once either directory exists, do not remove it or automatically retry the operation. A failure before claiming it reports `mutation_once_claimed=false` and `preflight_retry_available=true`; correct the reported preflight condition before any subsequent attempt.

Return each final JSON summary and retain its protected `REPORT.json`. Success must report `COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED`, the exact candidate manifest/config, `content_and_unpack=PASS`, `cri_visibility=PASS`, and `old_references_preserved=true`. Success still has `ready_for_rollout=false`.

The package generator is `package-data-qa-progress-frontend-r2.py`; its focused offline suite is `test_data_qa_progress_frontend_r2.py`. Production node capacity, import success, fresh UID checks, and browser acceptance require field evidence.
