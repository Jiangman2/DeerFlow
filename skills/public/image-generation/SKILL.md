---
name: image-generation
description: Generate a new image from a text description with the platform-managed image model. Use when the user asks to create, draw, render, visualize, or design an image, illustration, scene, character, product visual, or poster background.
allowed-tools:
  - generate_image
---

# Platform Image Generation

Use the built-in `generate_image` tool. The platform owns the endpoint,
credentials, request limits, output persistence, and artifact publication.

## Workflow

1. Turn the user's request into one complete English visual prompt. Preserve all
   requested subjects, style, composition, lighting, colors, text constraints,
   and exclusions. Do not silently add brands, people, or copyrighted
   characters that the user did not request.
2. Choose a supported pixel size. Both dimensions must be multiples of 32;
   `1024x1024` is the safe default.
3. Call `generate_image` once with that prompt. Use `negative_prompt` only when
   it materially improves the request. Use a meaningful filename when the
   subject is clear.
4. The tool publishes the finished image itself. Do not call Bash, provider
   SDKs, direct HTTP requests, or `present_files` for the same image.
5. Briefly report the result and offer a targeted revision if appropriate.

## Tool Contract

- `response_format` and result count are enforced by the platform: one
  `b64_json` image per call.
- Supported formats are `jpg`, `png`, and `webp`.
- `seed` may be used for reproducibility.
- The deployed API is text-to-image. Do not claim reference-image conditioning
  or invent a reference-image parameter.
- A tool error is authoritative. Report its bounded message; do not inspect
  credentials, discover models, or switch to another provider.

## Safety and Fidelity

- Follow the user's language in the response, but write the model prompt in
  English unless exact visible non-English text is required.
- If exact typography is essential, warn that raster generation may misspell
  text and prefer generating a clean background for later typesetting.
- Do not present a placeholder, stock image, or previously generated file as a
  successful current result.
