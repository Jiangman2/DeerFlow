import {
  CompassIcon,
  ImageIcon,
  MicroscopeIcon,
  PenLineIcon,
  ShapesIcon,
  SparklesIcon,
  VideoIcon,
} from "lucide-react";

import type { Translations } from "./types";

export const enUS: Translations = {
  portalTools: {
    title: "More office tools",
    userGuide: "User guide (opens in a new tab)",
    ppt: "PPT Assistant",
    proofread: "Proofreading",
    translate: "Translation",
    liveTranscribe: "Live Transcription",
    convert: "PDF Conversion",
    recordTranscribe: "Audio Transcription",
    speech: "Speech Synthesis",
  },
  // Locale meta
  locale: {
    localName: "English",
  },

  // Common
  common: {
    home: "Home",
    settings: "Settings",
    delete: "Delete",
    edit: "Edit",
    rename: "Rename",
    share: "Share",
    openInNewWindow: "Open in new window",
    close: "Close",
    showBrowser: "Show browser",
    more: "More",
    search: "Search",
    loadMore: "Load more",
    download: "Download",
    thinking: "Thinking",
    artifacts: "Artifacts",
    noArtifactSelected: "No artifact selected",
    selectArtifactToView: "Select an artifact to view its details",
    todos: "To-dos",
    public: "Public",
    custom: "Custom",
    notAvailableInDemoMode: "Not available in demo mode",
    loading: "Loading...",
    version: "Version",
    lastUpdated: "Last updated",
    code: "Code",
    preview: "Preview",
    cancel: "Cancel",
    save: "Save",
    install: "Install",
    create: "Create",
    import: "Import",
    export: "Export",
    exportAsMarkdown: "Export as Markdown",
    exportAsJSON: "Export as JSON",
    exportSuccess: "Conversation exported",
    regenerate: "Regenerate",
    branch: "Branch conversation",
    likeResponse: "Like response",
    dislikeResponse: "Dislike response",
    showArtifacts: "Show artifacts of this conversation",
  },

  browser: {
    title: "Browser",
    back: "Back",
    forward: "Forward",
    addressPlaceholder: "Enter a URL and press Enter",
    stopLive: "Stop live control",
    startLive: "Take live control",
    live: "Live",
    viewAlt: "Browser view",
    connecting: "Connecting to live browser…",
    noActivity: "No browser activity yet",
    waitingForFrame: "Waiting for the first live frame.",
    emptyDescription:
      "Enter a URL above or let the digital employee browse — the live view will appear here.",
    cannotOpen: "Cannot open {url}",
    noScreenshot: "Navigated, but no screenshot could be captured.",
    sidePanelDescription: "Browse the side panel for this conversation.",
  },

  // Home
  home: {
    docs: "Docs",
    blog: "Blog",
  },

  // Welcome
  welcome: {
    greeting: "Hello, again!",
    description:
      "Welcome to 本平台, an open source super agent. With built-in and custom skills, 本平台 helps you search on the web, analyze data, and generate artifacts like slides, web pages and do almost anything.",
    mobileDescription:
      "Your intelligent research assistant for oil and gas work",

    createYourOwnSkill: "Create Your Own Skill",
    createYourOwnSkillDescription:
      "Describe the work you want the agent to perform, and we will help you build a reusable skill.",
    hainengPortal: "Haineng AI Platform",
    totalUsage: "Total usage",
    todayUsage: "Usage today",
    todayUsers: "Today's users",
    todayTasks: "Today's tasks",
    supportGroupTitle: "Haineng Work support group",
    supportGroupName: "CNOOC general-purpose agent feedback group",
    supportGroupQrAlt: "Haineng Work support group QR code",
    supportGroupScan: "Scan with mobile Haineng Work to join",
    usageUnavailable: "Usage unavailable",
    contactUs: "Contact us",
    feedback: "Feedback",
    supportPhone: "Support phone",
    supportEmail: "Support email",
    generalAgentEmail: "General-purpose agent",
    aiPlatformEmail: "AI platform",
    businessOwner: "Business owner",
    operationsOwner: "Operations owner",
    serviceHours: "Service hours",
  },

  // Clipboard
  clipboard: {
    copyToClipboard: "Copy to clipboard",
    copiedToClipboard: "Copied to clipboard",
    failedToCopyToClipboard: "Failed to copy to clipboard",
    linkCopied: "Link copied to clipboard",
  },

  // Citations
  citations: {
    sourcesSummary: (count) =>
      `Used ${count} ${count === 1 ? "source" : "sources"}`,
    citeCount: (count) => `${count} ${count === 1 ? "cite" : "cites"}`,
    copyReference: (title) => `Copy ${title} reference`,
    copiedReference: (title) => `Copied ${title} reference`,
  },

  artifactPreview: {
    preparing: "Preparing preview...",
    loadingPages: "Loading preview pages...",
    loadFailed: "The preview could not be loaded.",
    retry: "Try again",
  },

  // Workspace Changes
  workspaceChanges: {
    title: "Workspace changes",
    editedTitle: (count) => `Edited ${count} ${count === 1 ? "file" : "files"}`,
    badge: (count, additions, deletions) =>
      `${count} ${count === 1 ? "file" : "files"} changed +${additions} -${deletions}`,
    viewChanges: "View changes",
    showMoreFiles: (count) =>
      `Show ${count} more ${count === 1 ? "file" : "files"}`,
    showFewerFiles: "Show fewer files",
    created: "Created",
    modified: "Modified",
    deleted: "Deleted",
    openFile: "Open file",
    loading: "Loading workspace changes...",
    noChanges: "No workspace changes recorded.",
    diffUnavailable: "Diff unavailable",
    binaryUnavailable: "Binary file. Diff unavailable.",
    largeUnavailable: "Large file. Diff omitted.",
    sensitiveUnavailable: "Sensitive path. Content hidden.",
    truncatedUnavailable: "Diff omitted because the change set is too large.",
    truncatedSummary: "Some changes were truncated.",
    symlinkUnavailable: "Symlink change. Diff unavailable.",
  },

  // Input Box
  inputBox: {
    placeholder: "How can I assist you today?",
    createSkillPrompt:
      "We're going to build a new skill step by step with `skill-creator`. To start, what do you want this skill to do?",
    addAttachments: "Add attachments",
    addShopCartAttachments: "Add from Data Lake",
    shopCart: "Data Lake",
    dataLakeFileList: "Data Lake file list",
    disableDataLake: "Do not use Data Lake",
    enableDataLake: "Use Data Lake",
    dataLakeAuthorizationFailed:
      "Could not obtain Data Lake access. Please try again.",
    dataLakePermissionDenied: "No Data Lake permission",
    dataLakeLoginRequired: "Please sign in to Data Lake first.",
    shopCartDialogTitle: "Select attachments from Data Lake",
    shopCartDialogDescription:
      "Data Lake opened in a new window. Select files there, then return here to add the selected files.",
    shopCartWindowDescription:
      "If the Data Lake window did not open, reopen it. After selecting files, keep that window open and add the selected files here.",
    shopCartReopen: "Reopen",
    shopCartPopupBlocked:
      "The Data Lake window was blocked. Allow pop-ups and try again.",
    newShopCartDialogDescription:
      "Select files from the Data Lake and add them as chat attachments.",
    newShopCartLoading: "Loading Data Lake files...",
    newShopCartEmpty: "No files in the Data Lake",
    newShopCartRefresh: "Refresh",
    newShopCartLoadFailed: "Failed to load Data Lake files. Please retry.",
    newShopCartComplete: "Ready",
    newShopCartProcessing: "Processing",
    newShopCartSelectionSummary: (selected: number, total: number) =>
      `${selected} / ${total} files selected`,
    shopCartAddSelected: "Add selected files",
    shopCartAdding: "Reading...",
    shopCartEmptySelection: "Select at least one Data Lake file",
    shopCartNoResponse: "Could not get Data Lake files. Please retry.",
    shopCartInvalidPayload:
      "The Data Lake returned an unsupported data format.",
    shopCartDownloading: "Downloading Data Lake files...",
    shopCartDownloadFailed: "Failed to download Data Lake files. Please retry.",
    shopCartPartialDownloadFailed: (failed: number) =>
      `${failed} Data Lake file${failed === 1 ? "" : "s"} failed to download.`,
    shopCartUnknownFiles:
      "Some Data Lake files were not recognized and were skipped.",
    shopCartNoKnownFiles:
      "None of the selected Data Lake files were recognized.",
    shopCartAddedFiles: (count: number) =>
      `Added ${count} Data Lake attachment${count === 1 ? "" : "s"}`,
    inputPolish: "Polish input",
    inputPolishing: "Polishing input...",
    inputPolishNoChanges: "This input is already clear.",
    inputPolishFailed: "Failed to polish input.",
    inputPolishUndo: "Undo polish",
    inputPolishCancel: "Cancel polishing",
    voiceInputStartLabel: "Dictate with voice",
    voiceInputStopLabel: "Stop voice input",
    voiceInputStart:
      "Dictate with voice. Audio is streamed through Gateway to the enterprise Qwen ASR service.",
    voiceInputStop: "Stop voice input",
    voiceInputListening: "Listening... Click to stop voice input.",
    voiceInputUnsupported:
      "This browser does not support secure recording or WebSocket voice input.",
    voiceInputInsecureContext:
      "This page is not a secure context, so the browser blocks microphone access. Use HTTPS or an approved trusted-origin browser policy.",
    voiceInputPermissionDenied:
      "Microphone access was denied. Allow microphone access and try again.",
    voiceInputMicrophoneUnavailable:
      "No microphone was detected. Check your device input and try again.",
    voiceInputAuthenticationRequired:
      "Your session has expired. Sign in again to use voice input.",
    voiceInputFinalTimeout: "Voice transcription timed out. Please try again.",
    voiceInputUnsupportedLanguage:
      "Voice input does not support the current language in this browser.",
    voiceInputNetworkError:
      "Voice input could not reach the enterprise transcription service.",
    voiceInputNoSpeech: "No speech was detected. Please try again.",
    voiceInputFailed: "Voice input failed. Please try again.",
    mode: "Mode",
    flashMode: "Flash",
    flashModeDescription: "Fast and efficient, but may not be accurate",
    reasoningMode: "Reasoning",
    reasoningModeDescription:
      "Reasoning before action, balance between time and accuracy",
    proMode: "Pro",
    proModeDescription:
      "Reasoning, planning and executing, get more accurate results, may take more time",
    ultraMode: "Ultra",
    ultraModeDescription:
      "Pro mode with subagents to divide work; best for complex multi-step tasks",
    reasoningEffort: "Reasoning Effort",
    reasoningEffortMinimal: "Minimal",
    reasoningEffortMinimalDescription: "Retrieval + Direct Output",
    reasoningEffortLow: "Low",
    reasoningEffortLowDescription: "Simple Logic Check + Shallow Deduction",
    reasoningEffortMedium: "Medium",
    reasoningEffortMediumDescription:
      "Multi-layer Logic Analysis + Basic Verification",
    reasoningEffortHigh: "High",
    reasoningEffortHighDescription:
      "Full-dimensional Logic Deduction + Multi-path Verification + Backward Check",
    searchModels: "Search models...",
    surpriseMe: "Surprise",
    surpriseMePrompt:
      "/surprise-me Combine the currently available skills into a creative, visually engaging, and genuinely surprising deliverable for me.",
    followupLoading: "Generating follow-up questions...",
    followupConfirmTitle: "Send suggestion?",
    followupConfirmDescription:
      "You already have text in the input. Choose how to send it.",
    followupConfirmAppend: "Append & send",
    followupConfirmReplace: "Replace & send",
    suggestionPlaceholderRequired:
      "Replace the suggestion placeholder before sending.",
    goalCommandDescription: "Set, show, or clear an active goal",
    compactCommandDescription:
      "Compact earlier context while keeping the full chat visible",
    goalLabel: "Goal",
    goalContinuing: "Continuing {count}/{max}",
    goalContinuationTooltip:
      "Auto-continued {count}/{max} times toward the goal; stops at the limit.",
    goalSet: "Goal set.",
    goalCleared: "Goal cleared.",
    goalNone: "No active goal.",
    goalActive: "Active goal: {goal}",
    goalFailed: "Goal command failed.",
    compactSuccess:
      "Earlier context compacted. The full chat remains visible; future model calls will use the summary and recent messages.",
    compactSkipped: "The current context does not need compaction yet.",
    compactFailed: "Context compaction failed.",
    suggestions: [
      {
        suggestion: "Image Generation",
        prompt:
          "/image-generation Generate a high-quality image centered on [topic], choosing an appropriate visual style, composition, color palette, and lighting.",
        icon: ImageIcon,
      },
      {
        suggestion: "Presentation",
        prompt:
          "/ppt-master Create a high-quality 5-slide presentation about [topic].",
        icon: ShapesIcon,
        menu: "ppt",
      },
      {
        suggestion: "Deep Research",
        prompt:
          "/deep-research Conduct systematic, in-depth, multi-angle research on [topic], verify key facts, and summarize the evidence, findings, and conclusions.",
        icon: MicroscopeIcon,
      },
      {
        suggestion: "Data Analysis",
        prompt:
          "/data-analysis Analyze my uploaded Excel or CSV file, inspect its structure and quality first, then produce statistical summaries, key metrics, anomalies, and reusable insights.",
        icon: PenLineIcon,
      },
    ],
    pptRouteMenuTitle: "Choose a PPT workflow",
    pptRouteSuggestions: [
      {
        id: "generate",
        suggestion: "Generate PPTX",
        description:
          "Create a new presentation from a topic, sources, or an existing deck",
        prompt:
          "/ppt-master Create a high-quality 5-slide presentation about [topic].",
        icon: ShapesIcon,
      },
      {
        id: "create-template",
        suggestion: "Create Template",
        description:
          "Convert reference material into a reusable template workspace",
        prompt:
          "/ppt-master Convert my uploaded PPTX into a reusable Deck template workspace.",
        icon: CompassIcon,
      },
      {
        id: "fill-native",
        suggestion: "Fill Native PPTX",
        description:
          "Preserve the native master, layouts, and slide shells while filling content",
        prompt:
          "/ppt-master Use my uploaded PPTX template, preserve its native master, layouts, and slide structure, and replace the content with [topic].",
        icon: PenLineIcon,
      },
      {
        id: "enhance-native",
        suggestion: "Enhance Native PPTX",
        description:
          "Keep the visible slides unchanged and add notes, audio, timings, and transitions",
        prompt:
          "/ppt-master Keep my uploaded PPTX's visible content and design unchanged, and add speaker notes, narration, auto-advance timings, and slide transitions.",
        icon: SparklesIcon,
      },
    ],
    suggestionsCreate: [
      {
        suggestion: "Webpage",
        prompt:
          "/frontend-design Create a distinctive, responsive, production-ready webpage centered on [topic].",
        icon: CompassIcon,
      },
      {
        suggestion: "Video",
        prompt:
          "/video-generation Generate a high-quality video centered on [topic], with an appropriate shot plan, visual style, motion, and atmosphere.",
        icon: VideoIcon,
      },
      {
        type: "separator",
      },
      {
        suggestion: "Skill",
        prompt:
          "/skill-creator Help me create a new skill. First ask what problem it should solve, its use cases, and expected deliverables, then guide me through design, implementation, and validation.",
        icon: SparklesIcon,
      },
    ],
    pleaseWaitStreaming: "Please wait for the current response to finish.",
  },

  // Sidebar
  sidebar: {
    newChat: "New chat",
    chats: "Chats",
    channels: "Channels",
    recentChats: "Recent chats",
    demoChats: "Demo chats",
    agents: "Digital Employees",
    agentSquare: "Agent Square",
    knowledge: "Knowledge",
    skillCenter: "Skill Center",
    openSkillCenter: "Open Skill Center in a new window",
    scheduledTasks: "Scheduled tasks",
    agentsDisabledTooltip: "Feature not enabled",
  },

  // Scheduled tasks
  scheduledTasks: {
    scheduleType: {
      cron: "Recurring",
      once: "One-time",
      interval: "Interval",
    },
    preset: {
      label: "Repeat",
      hourly: "Hourly",
      daily: "Daily",
      weekly: "Weekly",
      monthly: "Monthly",
      custom: "Custom cron",
    },
    fields: {
      minute: "Minute",
      time: "Time",
      weekday: "On",
      dayOfMonth: "Day of month",
      cron: "Cron expression",
      cronPlaceholder: "0 9 * * *",
      cronHint:
        "Use 5 fields: minute hour day month weekday. Every minute is * * * * * or */1 * * * *; 1 * * * * means the 1st minute of every hour.",
      runAt: "Run at",
      timezone: "Timezone",
      timezoneShanghai: "Beijing time (Asia/Shanghai)",
      intervalAmount: "Repeat every",
      intervalUnitSeconds: "Seconds",
      intervalUnitMinutes: "Minutes",
      intervalUnitHours: "Hours",
      intervalMinHint: "The minimum interval is 60 seconds.",
    },
    weekdays: {
      mon: "Mon",
      tue: "Tue",
      wed: "Wed",
      thu: "Thu",
      fri: "Fri",
      sat: "Sat",
      sun: "Sun",
    },
    preview: "Preview",
    cronHelp: "Optional: validate with crontab.guru",
    model: {
      label: "Execution model",
      placeholder: "Select a model",
      hint: "The model is saved with this task and will not change when you switch models in chat. New enterprise tasks default to DeepSeek V4 Flash.",
      unset: "Not set",
    },
    create: {
      title: "Create scheduled task",
      description:
        "Set a prompt and a schedule. The assistant will launch the task in the background without asking follow-up questions.",
      taskTitle: "Task title",
      prompt: "Prompt",
      agent: "Agent",
      leadAgent: "Default agent",
      submit: "Create",
      fillRequired: "Fill all required fields",
      adviceTitle: "Creation tips",
      adviceFresh:
        "Fresh thread is best for recurring digests, reports, and monitoring summaries.",
      adviceReuse:
        "Reuse thread is best when every run should continue the same topic.",
      adviceCron: "Cron uses 5 positions to describe when a task repeats.",
      adviceCronFormat: "minute, hour, day of month, month, weekday",
      adviceCronExamples: [
        "* * * * *: run every minute.",
        "*/5 * * * *: run every 5 minutes.",
        "0 9 * * *: run every day at 09:00.",
        "30 8 * * 1: run every Monday at 08:30.",
        "1 * * * *: run at minute 1 of every hour; it does not mean every minute.",
      ],
      adviceCronExternal:
        "If crontab.guru is unavailable on the intranet, use these examples directly.",
    },
    context: {
      fresh: "Fresh thread",
      reuse: "Reuse thread",
      threadIdPlaceholder: "Thread ID",
      reuseNoticeTitle: "Uses this thread's conversation history",
      reuseNoticeDescription:
        "If this thread has an active run at the scheduled time, DeerFlow queues this occurrence and starts it when the thread is available. It fails if the configured queue wait limit is exceeded.",
    },
    filters: {
      label: "Filters",
      allStatuses: "All statuses",
      enabled: "Enabled",
      paused: "Paused",
      completed: "Completed",
      failed: "Failed",
      allTypes: "All types",
      cron: "Cron",
      once: "Once",
      interval: "Interval",
    },
    detail: {
      contextMode: "Context mode",
      thread: "Thread",
      lastThread: "Last thread",
      schedule: "Schedule",
      expression: "Expression",
      nextRun: "Next run",
      lastRun: "Last run",
      lastRunId: "Last run id",
      lastError: "Last error",
      runsCount: "{count} runs",
      runsCountOne: "{count} run",
      noRuns: "No runs yet",
      noSelection: "No scheduled task selected",
      emptyTitle: "No scheduled tasks yet",
      emptyDescription:
        "Create a scheduled task to run prompts automatically and track status, next run time, and history here.",
      filteredByThread: "Filtered by thread: {id}",
      loadFailed: "Failed to load scheduled tasks",
      agent: "Agent",
    },
    actions: {
      edit: "Edit",
      cancelEdit: "Cancel edit",
      pause: "Pause",
      resume: "Resume",
      trigger: "Trigger now",
      delete: "Delete",
      duplicate: "Duplicate",
      duplicateTitleSuffix: " (Copy)",
    },
    deleteConfirm:
      "Are you sure you want to delete this scheduled task? This action cannot be undone.",
    errors: {
      create: "Failed to create scheduled task",
      update: "Failed to update scheduled task",
      pause: "Failed to pause scheduled task",
      resume: "Failed to resume scheduled task",
      trigger: "Failed to trigger scheduled task",
      delete: "Failed to delete scheduled task",
    },
    edit: {
      titlePlaceholder: "Edit title",
      promptPlaceholder: "Edit prompt",
      submit: "Save edit",
    },
    status: {
      enabled: "Enabled",
      paused: "Paused",
      running: "Running",
      completed: "Completed",
      failed: "Failed",
      cancelled: "Cancelled",
    },
    runTrigger: { scheduled: "scheduled", manual: "manual" },
    runStatus: {
      queued: "Queued",
      launching: "Launching",
      running: "Running",
      success: "Success",
      failed: "Failed",
      skipped: "Skipped",
      interrupted: "Interrupted",
    },
    history: {
      navigation: "Run history pagination",
      newer: "Newer",
      older: "Older",
      latest: "Latest",
      page: "Page {page}",
      paused: "Live refresh is paused while viewing older history.",
      loading: "Loading run history…",
      loadFailed: "Failed to load run history.",
      retry: "Retry",
    },
    recipes: {
      label: "Quick create",
      trending: {
        title: "GitHub Trending daily",
        desc: "Summarize today's top 10 trending repos",
      },
      news: {
        title: "Daily tech news digest",
        desc: "Collect and summarize the day's top tech news",
      },
      energyIntel: {
        title: "Daily energy tech intelligence",
        desc: "Track energy technology and oilfield AI updates",
      },
      weekly: {
        title: "Weekly report",
        desc: "Compile a weekly summary, every Monday",
      },
    },
  },

  // Agents
  agents: {
    title: "Digital Employees",
    description:
      "Discover and manage digital employees with dedicated roles, knowledge, and capabilities.",
    newAgent: "New Digital Employee",
    emptyTitle: "No digital employees yet",
    emptyDescription:
      "Create your first digital employee with a dedicated role and capabilities.",
    featureDisabledTitle: "Digital Employees is not enabled",
    featureDisabledDescription:
      "This feature is not enabled on this server. Please contact your administrator.",
    myAgents: "My digital employees",
    agentSquare: "Digital Employee Square",
    squareEmptyTitle: "No shared digital employees yet",
    squareEmptyDescription:
      "Published team digital employees and enterprise tools will appear here.",
    openExternal: "Start work",
    publishToSquare: "Publish to Digital Employee Square",
    unpublishFromSquare: "Remove from Digital Employee Square",
    unpublishConfirm:
      "Are you sure you want to remove this digital employee from the square?",
    publishSuccess: "Digital employee published to the square",
    unpublishSuccess: "Digital employee removed from the square",
    chat: "Start work",
    viewProfile: "View profile",
    profileTitle: "Digital employee profile",
    profileDescription:
      "Review this digital employee's description, category, and capabilities.",
    toolGroups: "Tool groups",
    skills: "Skills",
    delete: "Delete",
    deleteConfirm:
      "Are you sure you want to delete this digital employee? This action cannot be undone.",
    deleteSuccess: "Digital employee deleted",
    newChat: "New chat",
    createPageTitle: "New Digital Employee",
    createPageSubtitle:
      "Describe the role and working style, and I'll help configure the digital employee.",
    nameStepTitle: "Create a digital employee",
    nameStepHint:
      "Choose a portrait and name first, then refine responsibilities and capabilities in conversation.",
    nameStepPlaceholder: "e.g. monthly-report-expert",
    avatarPickerLabel: "Choose a digital employee avatar",
    avatarPickerHint:
      "Choose a bundled cartoon avatar; you can upload a custom avatar after creation.",
    expandAvatars: "Show all {count} avatars",
    collapseAvatars: "Show fewer avatars",
    nameStepContinue: "Continue",
    nameStepInvalidError:
      "Invalid name — use only Chinese characters, letters, digits, and hyphens",
    nameStepAlreadyExistsError:
      "A digital employee with this name already exists",
    nameStepNetworkError:
      "Network request failed — check your network or backend connection",
    nameStepCheckError: "Could not verify name availability — please try again",
    nameStepCheckErrorWithDetail: "Name check failed: {detail}",
    nameStepApiDisabledError:
      "Digital employee management is not enabled on this server. Please contact your administrator.",
    builderModelLabel: "Builder model",
    currentBuilderModel: "Current builder model",
    changeBuilderModel: "Change model",
    builderModelDialogTitle: "Choose digital employee builder model",
    nameStepBootstrapMessage:
      "The new digital employee's name is {name}. Help me design its responsibilities, behavior, and SOUL.md before saving it.",
    initialDescription: "Initial description: {value}",
    initialCategory: "Initial business domain: {value}",
    initialTags: "Initial tags: {value}",
    save: "Save digital employee",
    saving: "Saving digital employee...",
    saveRequested:
      "Save requested. 本平台 is generating and saving an initial digital employee now.",
    saveHint:
      "You can save this digital employee at any time from the top-right menu, even if this is only a first draft.",
    saveCommandMessage:
      "Please save this custom digital employee now based on everything we have discussed so far. Treat this as my explicit confirmation to save. If some details are still missing, make reasonable assumptions, generate a concise first SOUL.md in English, and call setup_agent immediately without asking me for more confirmation.",
    agentCreatedPendingRefresh:
      "The digital employee was created, but 本平台 could not load it yet. Please refresh this page in a moment.",
    more: "More actions",
    agentCreated: "Digital employee created!",
    startChatting: "Start work",
    backToGallery: "Back to Digital Employees",
    personal: "Personal",
    enterprise: "Enterprise",
    noDescription: "No description yet",
    capabilities: "Tags and capabilities",
    edit: "Edit details",
    settingsTitle: "Edit digital employee",
    settingsDescription:
      "Update the display name, description, business domain, tags, and avatar.",
    displayName: "Display name",
    stableName: "Internal name",
    descriptionLabel: "Description",
    descriptionPlaceholder:
      "For example: analyzes monthly operations and prepares reports",
    category: "Business domain",
    tags: "Tags",
    tagsPlaceholder: "Comma-separated, up to 8 tags",
    tagTooLong: "Each tag must be 20 characters or fewer",
    tagDuplicate: "That tag already exists",
    editTag: "Edit tag",
    deleteTag: "Delete tag",
    tagInputLabel: "Add or edit tag",
    tagLimitReached: "The 8-tag limit has been reached",
    addTag: "Add tag",
    confirmTagEdit: "Confirm tag edit",
    tagCount: "{count}/8 tags; up to 20 characters each",
    autoTag: "Suggest tags",
    inferenceSettings: "Dedicated model and inference settings",
    settingsModel: "Default model",
    settingsModelDefault: "Use global default",
    settingsTemperature: "Temperature",
    settingsTemperatureHint:
      "0 is deterministic; higher values are more creative (0–2)",
    settingsMaxTokens: "Max output tokens",
    settingsMaxTokensPlaceholder: "Inherit from model",
    settingsThinking: "Thinking mode",
    settingsThinkingOn: "On",
    settingsThinkingOff: "Off",
    settingsReasoningEffort: "Reasoning effort",
    settingsInherit: "Inherit",
    settingsInvalidTemperature: "Temperature must be between 0 and 2",
    settingsInvalidMaxTokens:
      "Max output tokens must be an integer from 1 to 200,000",
    updateSuccess: "Digital employee updated",
    avatarUploadHint:
      "PNG, JPEG, or WebP up to 5 MB; images are safely converted to WebP.",
    uploadAvatar: "Upload custom avatar",
    avatarTooLarge: "Avatar must be no larger than 5 MB",
    allCategories: "All business domains",
    businessDomainCount: "{count} business domains",
    expandCategories: "Expand business domains",
    collapseCategories: "Collapse business domains",
    unifiedDomains: "Unified (统) domains",
    unifiedDomainsHint: "Governed centrally by headquarters departments",
    distributedDomains: "Distributed (分) domains",
    distributedDomainsHint: "Implemented by the respective business units",
    popularTags: "Popular tags",
    expandTags: "Show all {count} tags",
    collapseTags: "Show fewer tags",
    resetFilters: "Reset",
    clearAll: "Clear filters",
    noTags: "No tags yet",
    searchPlaceholder: "Search name, tag, business domain, or capability",
    filters: "Filters",
    filtersHint: "Narrow digital employees by business domain and tag.",
    resultCount: "{count} digital employees",
    noResults: "No matching digital employees",
    noResultsHint: "Try another keyword or clear some filters.",
    loadMore: "Load more",
    paginationNavigation: "Digital employee pages",
    previousPage: "Previous",
    nextPage: "Next",
    pageNumber: "Page {page}",
    pageSummary: "Page {page} of {total}",
  },

  // Breadcrumb
  breadcrumb: {
    workspace: "Workspace",
    chats: "Chats",
    agents: "Digital Employees",
    knowledge: "Knowledge",
    skillCenter: "Skill Center",
    scheduledTasks: "Scheduled tasks",
  },

  // Workspace
  workspace: {
    settingsAndMore: "Settings and more",
    about: "About 本平台",
    logout: "Log out",
    gatewayUnavailable: "Gateway is temporarily unavailable.",
    gatewayUnavailableRetrying: "Retrying in the background…",
  },

  // Conversation
  conversation: {
    noMessages: "No messages yet",
    startConversation: "Start a conversation to see messages here",
    branchCreated: "Conversation branch created",
    branchFailed: "Failed to branch conversation.",
  },

  // Chats
  chats: {
    deleteChat: "Delete chat",
    deleteConfirm: (title) =>
      `Delete “${title}”? This will delete the conversation and its files. This action cannot be undone.`,
    deleteFailed: "Failed to delete chat. Please try again.",
    noActiveChats: "No recent chats",
    activeChats: "Recent chats",
    archivedChats: "Archived",
    archiveChat: "Archive chat",
    restoreChat: "Restore chat",
    archiveSuccess: "Chat archived",
    restoreSuccess: "Chat restored",
    archiveFailed: "Failed to update archived chat",
    archiveDescription:
      "Archiving keeps messages and files. Running and scheduled tasks continue.",
    undoArchive: "Undo",
    noArchivedChats: "No archived chats",
    noMatchingChats: "No matching chats in the loaded conversations",
    loadChatsFailed: "Failed to load conversations",
    retryLoadChats: "Retry",
    searchChats: "Search chats",
    loadMoreToSearch: "Load more to search older conversations",
    loadingMore: "Loading more...",
    loadOlderChats: "Load older chats",
    pinChat: "Pin chat",
    unpinChat: "Unpin chat",
    pinChatFailed: "Failed to update pinned chat",
  },

  // Sidecar
  sidecar: {
    title: "Side chat",
    open: "Open side chat",
    close: "Close side chat",
    delete: "Delete side chat",
    deleteConfirm:
      "Are you sure you want to delete this side chat? This action cannot be undone. To simply hide it, use the side chat toggle in the header instead.",
    deleteSuccess: "Side chat deleted",
    deleteFailed: "Failed to delete side chat.",
    addToConversation: "Add to conversation",
    askInSideChat: "Ask in side chat",
    reference: "Reference",
    selectedTextFragment: "{count} selected text fragment",
    selectedTextFragments: "{count} selected text fragments",
    clearReferences: "Clear selected references",
    emptyTitle: "Ask a follow-up",
    emptyDescription: "Ask a follow-up grounded in the referenced text.",
    placeholder: "Ask a deeper follow-up...",
    send: "Send",
    sendFailed: "Failed to send side chat message.",
    noContext: "No context selected",
    continuing: "Continue in this side chat",
    selectionCrossesMessages:
      "Selection spans multiple messages. Select text within a single reply to quote it.",
  },

  // Channels
  channels: {
    title: "Channels",
    connect: "Connect",
    modify: "Modify",
    reconnect: "Reconnect",
    disconnect: "Disconnect",
    connected: "Connected",
    notConnected: "Not connected",
    pending: "Pending",
    revoked: "Disconnected",
    disabled: "Disabled",
    unconfigured: "Not configured",
    unavailable: "Channel connections are unavailable right now.",
    unavailableShort: "Unavailable",
    setupTitle: (name: string) => `Connect ${name}`,
    setupEditTitle: (name: string) => `Modify ${name}`,
    setupDescription:
      "Enter the values needed by this server process. They are not written to config.yaml.",
    saveAndConnect: "Save and connect",
    saveChanges: "Save changes",
    descriptions: {
      telegram: "Telegram direct messages through your 本平台 bot.",
      slack: "Slack workspace messages and mentions.",
      discord: "Discord server messages through your 本平台 bot.",
      feishu: "Messages through your 海能 Work app.",
      dingtalk: "DingTalk Stream Push messages through your 本平台 bot.",
      wechat: "WeChat iLink messages through your 本平台 bot.",
      wecom: "WeCom messages through your 本平台 AI bot.",
    },
    connectedAs: (name: string) => `Connected as ${name}.`,
  },

  // Page titles (document title)
  pages: {
    appName: "中国海油通用智能体",
    chats: "Chats",
    newChat: "New chat",
    untitled: "Untitled",
  },

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => `${count} more step${count === 1 ? "" : "s"}`,
    lessSteps: "Less steps",
    executeCommand: "Execute command",
    presentFiles: "Present files",
    needYourHelp: "Need your help",
    useTool: (toolName: string) => `Use "${toolName}" tool`,
    searchFor: (query: string) => `Search for "${query}"`,
    searchForRelatedInfo: "Search for related information",
    searchForRelatedImages: "Search for related images",
    searchForRelatedImagesFor: (query: string) =>
      `Search for related images for "${query}"`,
    searchOnWebFor: (query: string) => `Search on the web for "${query}"`,
    viewWebPage: "View web page",
    listFolder: "List folder",
    readFile: "Read file",
    writeFile: "Write file",
    clickToViewContent: "Click to view file content",
    writeTodos: "Update to-do list",
    skillInstallTooltip: "Install skill and make it available to 本平台",
  },

  humanInput: {
    answered: "Answered",
    pending: "Sending...",
    readOnly: "Read only",
    otherLabel: "Other answer",
    otherPlaceholder: "Type another answer...",
    submit: "Submit",
    emptyError: "Enter an answer before submitting.",
    answeredValue: (value: string) => `Answered: ${value}`,
  },

  // Subtasks
  uploads: {
    uploading: "Uploading...",
    uploadingFiles: "Uploading files, please wait...",
    limitsHint: (maxFiles: number, maxFileSize: string, maxTotalSize: string) =>
      `Add attachments (up to ${maxFiles} files, ${maxFileSize} each, ${maxTotalSize} total). Most regular file types are supported; compress macOS .app bundles first.`,
    filesTooLarge: (files: string, maxFileSize: string) =>
      `Files exceeding the ${maxFileSize} per-file limit were not added: ${files}.`,
    tooManyFiles: (count: number, maxFiles: number) =>
      `${count} file${count === 1 ? " was" : "s were"} not added. You can attach up to ${maxFiles} files at once.`,
    totalSizeTooLarge: (count: number, maxTotalSize: string) =>
      `${count} file${count === 1 ? " was" : "s were"} not added. Attachments can total up to ${maxTotalSize}.`,
  },

  subtasks: {
    subtask: "Subtask",
    executing: (count: number) =>
      `Executing ${count === 1 ? "" : count + " "}subtask${count === 1 ? "" : "s in parallel"}`,
    in_progress: "Running subtask",
    completed: "Subtask completed",
    failed: "Subtask failed",
    incomplete: "Subtask not completed",
    viewStepDetails: "View details",
    hideStepDetails: "Hide details",
    toolInput: "Tool input",
    toolOutput: "Tool output",
  },

  // Token Usage
  tokenUsage: {
    title: "Token Usage",
    label: "Tokens",
    input: "Input",
    output: "Output",
    total: "Total",
    view: "Display",
    unavailable:
      "No token usage yet. Usage appears only after a successful model response when the provider returns usage_metadata.",
    unavailableShort: "No usage returned",
    collecting: "Collecting tokens",
    note: "Header totals use persisted thread usage, plus visible in-flight usage while a run is still streaming. Per-turn and debug usage come from currently visible messages only. Totals may differ from provider billing pages.",
    presets: {
      off: "Off",
      summary: "Summary",
      perTurn: "Per turn",
      debug: "Debug",
    },
    presetDescriptions: {
      off: "Hide token usage in the header and conversation.",
      summary: "Show only the current conversation total in the header.",
      perTurn:
        "Show the header total and one token summary per assistant turn.",
      debug: "Show the header total and step-level token debugging details.",
    },
    finalAnswer: "Final answer",
    stepTotal: "Step total",
    sharedAttribution: "Shared across multiple actions in this step",
    subagent: (description: string) => `Subagent: ${description}`,
    startTodo: (content: string) => `Start To-do: ${content}`,
    completeTodo: (content: string) => `Complete To-do: ${content}`,
    updateTodo: (content: string) => `Update To-do: ${content}`,
    removeTodo: (content: string) => `Remove To-do: ${content}`,
  },

  // Shortcuts
  shortcuts: {
    searchActions: "Search actions...",
    noResults: "No results found.",
    actions: "Actions",
    keyboardShortcuts: "Keyboard Shortcuts",
    keyboardShortcutsDescription:
      "Navigate 本平台 faster with keyboard shortcuts.",
    openCommandPalette: "Open Command Palette",
    toggleSidebar: "Toggle Sidebar",
  },

  // Settings
  settings: {
    title: "Settings",
    description: "Adjust how 本平台 looks and behaves for you.",
    sections: {
      account: "Account",
      appearance: "Appearance",
      channels: "Channels",
      memory: "Memory",
      tools: "Tools",
      skills: "Skills",
      notification: "Notification",
      about: "About",
    },
    memory: {
      title: "Memory",
      description:
        "DeerFlow automatically learns from your conversations in the background. These memories help DeerFlow understand you better and deliver a more personalized experience.",
      updateBehaviorHint:
        "Memory updates in the background after ordinary conversations and changes only when reusable information is found. Conversations with an explicit knowledge-base scope do not write to personal memory.",
      empty: "No memory data to display.",
      rawJson: "Raw JSON",
      exportButton: "Export memory",
      exportSuccess: "Memory exported",
      importButton: "Import memory",
      importConfirmTitle: "Import memory?",
      importConfirmDescription:
        "This will overwrite your current memory with the selected JSON backup.",
      importFileLabel: "Selected file",
      importInvalidFile:
        "Failed to read the selected memory file. Please choose a valid JSON export.",
      importSuccess: "Memory imported",
      manualFactSource: "Manual",
      addFact: "Add fact",
      addFactTitle: "Add memory fact",
      editFactTitle: "Edit memory fact",
      addFactSuccess: "Fact created",
      editFactSuccess: "Fact updated",
      clearAll: "Clear all memory",
      clearAllConfirmTitle: "Clear all memory?",
      clearAllConfirmDescription:
        "This will remove all saved summaries and facts. This action cannot be undone.",
      clearAllSuccess: "All memory cleared",
      factDeleteConfirmTitle: "Delete this fact?",
      factDeleteConfirmDescription:
        "This fact will be removed from memory immediately. This action cannot be undone.",
      factDeleteSuccess: "Fact deleted",
      factContentLabel: "Content",
      factCategoryLabel: "Category",
      factConfidenceLabel: "Confidence",
      factContentPlaceholder: "Describe the memory fact you want to save",
      factCategoryPlaceholder: "context",
      factConfidenceHint: "Use a number between 0 and 1.",
      factSave: "Save fact",
      factValidationContent: "Fact content cannot be empty.",
      factValidationConfidence: "Confidence must be a number between 0 and 1.",
      noFacts: "No saved facts yet.",
      summaryReadOnly:
        "Summary sections are read-only for now. You can currently add, edit, or delete individual facts, or clear all memory.",
      memoryFullyEmpty: "No memory saved yet.",
      factPreviewLabel: "Fact to delete",
      searchPlaceholder: "Search memory",
      filterAll: "All",
      filterFacts: "Facts",
      filterSummaries: "Summaries",
      noMatches: "No matching memory found.",
      markdown: {
        overview: "Overview",
        userContext: "User context",
        work: "Work",
        personal: "Personal",
        topOfMind: "Top of mind",
        historyBackground: "History",
        recentMonths: "Recent months",
        earlierContext: "Earlier context",
        longTermBackground: "Long-term background",
        updatedAt: "Updated at",
        facts: "Facts",
        empty: "(empty)",
        table: {
          category: "Category",
          confidence: "Confidence",
          confidenceLevel: {
            veryHigh: "Very high",
            high: "High",
            normal: "Normal",
            unknown: "Unknown",
          },
          content: "Content",
          source: "Source",
          createdAt: "CreatedAt",
          view: "View",
        },
      },
    },
    appearance: {
      themeTitle: "Theme",
      themeDescription:
        "Choose how the interface follows your device or stays fixed.",
      system: "System",
      light: "Light",
      dark: "Dark",
      systemDescription: "Match the operating system preference automatically.",
      lightDescription: "Bright palette with higher contrast for daytime.",
      darkDescription: "Dim palette that reduces glare for focus.",
      languageTitle: "Language",
      languageDescription: "Switch between languages.",
    },
    tools: {
      title: "Tools",
      description: "Manage the configuration and enabled status of MCP tools.",
      adminRequired: "Admin privileges are required to manage MCP tools.",
      empty: "No MCP tools configured.",
    },
    channels: {
      title: "Channels",
      description:
        "Connect IM accounts that can send messages to 本平台 from outside the browser.",
      disabled:
        "Channel connections are not enabled on this server. Ask an administrator to enable channel_connections.",
    },
    skills: {
      title: "Agent Skills",
      description:
        "View the configuration and enabled status of the agent skills.",
      publicReadOnly:
        "Public skills are managed centrally by platform administrators. Other users can only view their enabled status.",
      createSkill: "Create skill",
      emptyTitle: "No agent skill yet",
      emptyDescription:
        "Put your agent skill folders under the `/skills/custom` folder under the root folder of 本平台.",
      emptyButton: "Create Your First Skill",
      adminRequired: "Admin privileges are required to manage agent skills.",
      installAdminRequired:
        "Admin privileges are required to install agent skills.",
    },
    notification: {
      title: "Notification",
      description:
        "本平台 only sends a completion notification when the window is not active. This is especially useful for long-running tasks so you can switch to other work and get notified when done.",
      requestPermission: "Request notification permission",
      deniedHint:
        "Notification permission was denied. You can enable it in your browser's site settings to receive completion alerts.",
      testButton: "Send test notification",
      testTitle: "本平台",
      testBody: "This is a test notification.",
      notSupported: "Your browser does not support notifications.",
      disableNotification: "Disable notification",
    },
    account: {
      profileTitle: "Profile",
      email: "Email",
      role: "Role",
      ssoProvider: "SSO",
      changePasswordTitle: "Change Password",
      changePasswordDescription: "Update your account password.",
      ssoPasswordDescription: "Password is managed by your SSO provider.",
      ssoPasswordMessage:
        "This account signs in with {provider}, so 本平台 cannot manage or change its password here. Use your SSO provider's account settings instead.",
      currentPassword: "Current password",
      newPassword: "New password",
      confirmNewPassword: "Confirm new password",
      passwordMismatch: "New passwords do not match",
      passwordTooShort: "Password must be at least 8 characters",
      passwordChangedSuccess: "Password changed successfully",
      networkError: "Network error. Please try again.",
      updating: "Updating...",
      updatePassword: "Update Password",
      signOut: "Sign Out",
    },
    acknowledge: {
      emptyTitle: "Acknowledgements",
      emptyDescription: "Credits and acknowledgements will show here.",
    },
  },
  login: {
    signInTitle: "Sign in to your account",
    createAccountTitle: "Create a new account",
    email: "Email",
    emailPlaceholder: "you@example.com",
    password: "Password",
    passwordPlaceholder: "•••••••",
    pleaseWait: "Please wait...",
    signIn: "Sign In",
    createAccount: "Create Account",
    createAdminAccount: "Create admin account",
    adminSetupRequiredTitle: "Administrator setup is required",
    adminSetupRequiredDescription:
      "本平台 needs an administrator account before new regular accounts can be created.",
    orContinueWith: "Or continue with",
    ssoHint:
      "If your account uses single sign-on, sign in with the option below instead.",
    continueWith: (provider: string) => `Continue with ${provider}`,
    noAccountSignUp: "Don't have an account? Sign up",
    haveAccountSignIn: "Already have an account? Sign in",
    backToHome: "← Back to home",
    networkError: "Network error. Please try again.",
    authFailed: "Authentication failed.",
    signingIn: "Signing you in...",
    redirecting: "Redirecting...",
    authFailedRedirecting: "Authentication failed. Redirecting to login...",
    errors: {
      sso_failed: "SSO login failed. Please try again or use email login.",
      sso_cancelled: "SSO login was cancelled.",
      sso_account_exists:
        "An account with this email already exists. Please sign in with your password or contact your administrator.",
      sso_not_allowed:
        "SSO login is not allowed for your account. Contact your administrator.",
    },
  },
};
