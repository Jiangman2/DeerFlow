# Enterprise sandbox pool release

The `r4/` tree contains the exact zipapp sources used for the November 27 rollout. Gateway and PPT Runner use separate overlays built against their respective production images. Run `python -B build.py --output deerflow-global-pool-170-chat-models-r4.pyz` to reconstruct the installed package. Use its `--status` command to inspect the current pool. The installer checks the deployment and runtime source baselines before patching workloads.

The Provisioner prioritizes warm slots for all users. It counts existing cold Pods against the target of 170, prepares at most two slots at a time and commissions a scoped mount worker for each assignment. Gateway removes DeepSeek-V4-Flash from the conversation model list and applies the selected chat model to title generation and built-in summarization.

`repair-r4-ppt-runner-revision-0023.py` supplies the verified Gateway migration to the matching Runner deployment through an immutable ConfigMap. It preserves the R4 release annotation so the waiting install can finish.

The public TLS entry runs on datax-01. The contents of `cnoocclaw-invite.inc` belong inside its shared HTTPS server. The host-specific rewrite preserves the existing route for heai. The Kubernetes Nginx template also includes the exact /join redirect. The incremental Kubernetes update script operates on the mounted template. After an entry-point change, verify that a GET for `/join?code=route-test` returns 302 and the relative Location `/platform/organizations?invite_code=route-test`.
