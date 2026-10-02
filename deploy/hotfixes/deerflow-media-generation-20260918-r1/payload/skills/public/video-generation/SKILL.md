---
name: video-generation
description: Generate a new text-to-video or image-to-video clip with the platform-managed video model. Use when the user asks to create, animate, render, or synthesize a video clip.
allowed-tools:
  - generate_video
---

# Platform Video Generation

Use the built-in asynchronous `generate_video` tool. The platform owns the
endpoint, optional credential, safe reference handling, durable job state,
download, and artifact publication.

## Workflow

1. Convert the request into one complete English shot prompt covering subject,
   action, setting, camera movement, composition, lighting, visual style, and
   temporal continuity.
2. Choose generation settings. Defaults are `832x480`, 24 fps, and 81 frames.
   Video dimensions must be divisible by 16. Use either `num_frames` or
   `seconds`, never both.
3. For text-to-video, call `generate_video` with the prompt and settings. For
   image-to-video, add exactly one of:
   - `reference_image`: a current-thread PNG/JPEG/WebP path under uploads,
     workspace, or outputs;
   - `reference_url`: a public HTTP(S) image URL.
4. If the result is pending, retain the returned `job_id` and call
   `generate_video` again with that `job_id`. Never resubmit the prompt merely
   because the first call did not finish within its bounded wait.
5. When completed, the tool downloads and publishes the MP4 itself. Do not call
   Bash, direct HTTP requests, provider SDKs, or `present_files` for that video.

## Job and Failure Contract

- Never invent or edit a `job_id`.
- Job ownership is scoped to the current thread; a job from another thread is
  rejected.
- The remote service keeps job records only in memory. If it restarts and loses
  a known job, report that condition instead of silently creating a duplicate.
- Deleting a remote record is not cancellation, so this Skill never uses the
  DELETE route.
- A failed/canceled result is terminal for that submitted job. Report the
  bounded error and ask before creating a new billable job.
- Do not inspect secrets, probe model catalogs, or switch providers.

## Output Quality

- Prefer one coherent shot for a short clip; avoid packing many scene changes
  into a few seconds.
- State motion and camera behavior explicitly and use `negative_prompt` for
  common temporal defects when useful.
- Do not claim synchronized dialogue, audio, or exact lettering unless the
  configured model actually returns it in the generated artifact.
