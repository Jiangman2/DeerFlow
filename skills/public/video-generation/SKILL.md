---
name: video-generation
description: Use this skill when the user requests to generate, create, or imagine videos through the platform-managed DashScope video service.
---

# Video Generation Skill

## Overview

This skill generates videos through the platform-managed DashScope service. It
uses a persisted asynchronous task so every sandbox command finishes before the
enterprise command-channel timeout.

## Mandatory Execution Contract

- Use only the bundled `generate.py` entry point shown below.
- Do not inspect the script, enumerate environment variables, print credentials,
  inspect installed SDKs, probe provider APIs, guess model names, or construct a
  direct HTTP request.
- Never put an API key, token, or Authorization header in a command or file.
  Credentials and the approved model are injected by the platform.
- DashScope is the only supported provider for this deployment. Do not try
  Gemini, MiniMax, stock-video sites, or any fallback provider.
- Start with exactly one `--submit-only` call. Every later call for that output
  must use `--status-only`.
- Never invoke the script without one of those two flags.

## Core Capabilities

- Create structured JSON prompts for AIGC video generation
- Generate text-to-video through the platform-managed script
- Persist provider task state to prevent duplicate billable submissions

## Workflow

### Step 1: Understand Requirements

When a user requests video generation, identify:

- Subject/content: What should be in the image
- Style preferences: Art style, mood, color palette
- Technical specs: Aspect ratio, composition, lighting
- You don't need to check the folder under `/mnt/user-data`

### Step 2: Create Structured Prompt

Generate a structured JSON file in `/mnt/user-data/workspace/` with naming pattern: `{descriptive-name}.json`

### Step 3: Submit Generation

Submit the asynchronous provider task and return immediately:
```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/prompt-file.json \
  --output-file /mnt/user-data/outputs/generated-video.mp4 \
  --aspect-ratio 16:9 \
  --submit-only
```

Parameters:

- `--prompt-file`: Absolute path to JSON prompt file (required)
- `--output-file`: Absolute path to output video file (required)
- `--aspect-ratio`: Aspect ratio of the generated video (optional, default: 16:9)
- `--submit-only`: Submit exactly once, persist the provider task ID, and return
  without waiting for the video

The command must report `VIDEO_TASK_SUBMITTED` or
`VIDEO_TASK_ALREADY_SUBMITTED`. Never delete its hidden task-state file and
never submit the same output path again automatically: an interrupted response
does not prove that the provider rejected the billable task.

### Step 4: Check Status in Short Calls

After submission, wait briefly in a separate bash call, then perform one status
check:

```bash
sleep 20
```

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/prompt-file.json \
  --output-file /mnt/user-data/outputs/generated-video.mp4 \
  --aspect-ratio 16:9 \
  --status-only
```

- `VIDEO_TASK_PENDING`: wait again, then run one new `--status-only` command.
- `VIDEO_TASK_SUCCEEDED`: the output file is ready; present it to the user.
- An explicit failed/canceled status: report that provider error. Do not
  automatically create another billable task.

Every status check must be a separate bash tool call. Do not wrap status checks
in a shell/Python polling loop and do not run the legacy foreground command in
enterprise AIO Sandbox: a long-lived `/v1/bash/exec` request can be terminated by
the Sandbox Nginx command-channel timeout.

[!IMPORTANT]
Call the script exactly as documented. Provider discovery and credential
inspection are prohibited and are not troubleshooting steps.

## Video Generation Example

User request: "Generate a short video clip depicting the opening scene from "The Chronicles of Narnia: The Lion, the Witch and the Wardrobe"

Step 1: Search for the opening scene of "The Chronicles of Narnia: The Lion, the Witch and the Wardrobe" online

Step 2: Create a JSON prompt file with the following content:

```json
{
  "title": "The Chronicles of Narnia - Train Station Farewell",
  "background": {
    "description": "World War II evacuation scene at a crowded London train station. Steam and smoke fill the air as children are being sent to the countryside to escape the Blitz.",
    "era": "1940s wartime Britain",
    "location": "London railway station platform"
  },
  "characters": ["Mrs. Pevensie", "Lucy Pevensie"],
  "camera": {
    "type": "Close-up two-shot",
    "movement": "Static with subtle handheld movement",
    "angle": "Profile view, intimate framing",
    "focus": "Both faces in focus, background soft bokeh"
  },
  "dialogue": [
    {
      "character": "Mrs. Pevensie",
      "text": "You must be brave for me, darling. I'll come for you... I promise."
    },
    {
      "character": "Lucy Pevensie",
      "text": "I will be, mother. I promise."
    }
  ],
  "audio": [
    {
      "type": "Train whistle blows (signaling departure)",
      "volume": 1
    },
    {
      "type": "Strings swell emotionally, then fade",
      "volume": 0.5
    },
    {
      "type": "Ambient sound of the train station",
      "volume": 0.5
    }
  ]
}
```

Step 3: Submit with generate.py
```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/narnia-farewell-scene.json \
  --output-file /mnt/user-data/outputs/narnia-farewell-scene-01.mp4 \
  --aspect-ratio 16:9 \
  --submit-only
```

Then use the separate `sleep` and `--status-only` calls described above until
the task succeeds.

The configured enterprise model is text-to-video. Do not pass
`--reference-images`. If the user requires image-to-video, explain that this
deployment does not currently expose that capability rather than probing
another provider.

## Output Handling

After generation:

- Videos are typically saved in `/mnt/user-data/outputs/`
- Share generated videos (come first) with user as well as generated image if applicable, using `present_files` tool
- Provide brief description of the generation result
- Offer to iterate if adjustments needed

## Notes

- Always use English for prompts regardless of user's language
- JSON format ensures structured, parsable prompts
- Reference image enhance generation quality significantly
- Iterative refinement is normal for optimal results

## Provider Policy

Provider selection is an operations concern, not an agent task. The platform
injects the approved DashScope credential, endpoint, model, duration,
resolution, proxy route, and CA configuration. If the bundled command reports a
provider or credential error, return that bounded error to the user. Do not
inspect secrets, probe endpoints, enumerate models, install SDKs, download
substitute stock footage, or switch providers.
