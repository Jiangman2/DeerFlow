import type { LucideIcon } from "lucide-react";

export interface Translations {
  // Locale meta
  locale: {
    localName: string;
  };

  // Common
  common: {
    home: string;
    settings: string;
    delete: string;
    edit: string;
    rename: string;
    share: string;
    openInNewWindow: string;
    close: string;
    showBrowser: string;
    more: string;
    search: string;
    loadMore: string;
    download: string;
    thinking: string;
    artifacts: string;
    noArtifactSelected: string;
    selectArtifactToView: string;
    todos: string;
    public: string;
    custom: string;
    notAvailableInDemoMode: string;
    loading: string;
    version: string;
    lastUpdated: string;
    code: string;
    preview: string;
    cancel: string;
    save: string;
    install: string;
    create: string;
    import: string;
    export: string;
    exportAsMarkdown: string;
    exportAsJSON: string;
    exportSuccess: string;
    regenerate: string;
    branch: string;
    likeResponse: string;
    dislikeResponse: string;
    showArtifacts: string;
  };

  browser: {
    title: string;
    back: string;
    forward: string;
    addressPlaceholder: string;
    stopLive: string;
    startLive: string;
    live: string;
    viewAlt: string;
    connecting: string;
    noActivity: string;
    waitingForFrame: string;
    emptyDescription: string;
    cannotOpen: string;
    noScreenshot: string;
    sidePanelDescription: string;
  };

  home: {
    docs: string;
    blog: string;
  };

  // Welcome
  welcome: {
    greeting: string;
    description: string;
    mobileDescription: string;
    createYourOwnSkill: string;
    createYourOwnSkillDescription: string;
  };

  // Clipboard
  clipboard: {
    copyToClipboard: string;
    copiedToClipboard: string;
    failedToCopyToClipboard: string;
    linkCopied: string;
  };

  // Citations
  citations: {
    sourcesSummary: (count: number) => string;
    citeCount: (count: number) => string;
    copyReference: (title: string) => string;
    copiedReference: (title: string) => string;
  };

  artifactPreview: {
    preparing: string;
    loadingPages: string;
    loadFailed: string;
    retry: string;
  };

  // Workspace Changes
  workspaceChanges: {
    title: string;
    editedTitle: (count: number) => string;
    badge: (count: number, additions: number, deletions: number) => string;
    viewChanges: string;
    showMoreFiles: (count: number) => string;
    showFewerFiles: string;
    created: string;
    modified: string;
    deleted: string;
    openFile: string;
    loading: string;
    noChanges: string;
    diffUnavailable: string;
    binaryUnavailable: string;
    largeUnavailable: string;
    sensitiveUnavailable: string;
    truncatedUnavailable: string;
    truncatedSummary: string;
    symlinkUnavailable: string;
  };

  // Input Box
  inputBox: {
    placeholder: string;
    createSkillPrompt: string;
    addAttachments: string;
    addShopCartAttachments: string;
    shopCart: string;
    dataLakeFileList: string;
    disableDataLake: string;
    enableDataLake: string;
    dataLakeAuthorizationFailed: string;
    dataLakePermissionDenied: string;
    dataLakeLoginRequired: string;
    shopCartDialogTitle: string;
    shopCartDialogDescription: string;
    shopCartWindowDescription: string;
    shopCartReopen: string;
    shopCartPopupBlocked: string;
    newShopCartDialogDescription: string;
    newShopCartLoading: string;
    newShopCartEmpty: string;
    newShopCartRefresh: string;
    newShopCartLoadFailed: string;
    newShopCartComplete: string;
    newShopCartProcessing: string;
    newShopCartSelectionSummary: (selected: number, total: number) => string;
    shopCartAddSelected: string;
    shopCartAdding: string;
    shopCartEmptySelection: string;
    shopCartNoResponse: string;
    shopCartInvalidPayload: string;
    shopCartDownloading: string;
    shopCartDownloadFailed: string;
    shopCartPartialDownloadFailed: (failed: number) => string;
    shopCartUnknownFiles: string;
    shopCartNoKnownFiles: string;
    shopCartAddedFiles: (count: number) => string;
    inputPolish: string;
    inputPolishing: string;
    inputPolishNoChanges: string;
    inputPolishFailed: string;
    inputPolishUndo: string;
    inputPolishCancel: string;
    voiceInputStartLabel: string;
    voiceInputStopLabel: string;
    voiceInputStart: string;
    voiceInputStop: string;
    voiceInputListening: string;
    voiceInputUnsupported: string;
    voiceInputInsecureContext: string;
    voiceInputPermissionDenied: string;
    voiceInputMicrophoneUnavailable: string;
    voiceInputAuthenticationRequired: string;
    voiceInputFinalTimeout: string;
    voiceInputUnsupportedLanguage: string;
    voiceInputNetworkError: string;
    voiceInputNoSpeech: string;
    voiceInputFailed: string;
    mode: string;
    flashMode: string;
    flashModeDescription: string;
    reasoningMode: string;
    reasoningModeDescription: string;
    proMode: string;
    proModeDescription: string;
    ultraMode: string;
    ultraModeDescription: string;
    reasoningEffort: string;
    reasoningEffortMinimal: string;
    reasoningEffortMinimalDescription: string;
    reasoningEffortLow: string;
    reasoningEffortLowDescription: string;
    reasoningEffortMedium: string;
    reasoningEffortMediumDescription: string;
    reasoningEffortHigh: string;
    reasoningEffortHighDescription: string;
    searchModels: string;
    surpriseMe: string;
    surpriseMePrompt: string;
    followupLoading: string;
    followupConfirmTitle: string;
    followupConfirmDescription: string;
    followupConfirmAppend: string;
    followupConfirmReplace: string;
    suggestionPlaceholderRequired: string;
    goalCommandDescription: string;
    compactCommandDescription: string;
    goalLabel: string;
    goalContinuing: string;
    goalContinuationTooltip: string;
    goalSet: string;
    goalCleared: string;
    goalNone: string;
    goalActive: string;
    goalFailed: string;
    compactSuccess: string;
    compactSkipped: string;
    compactFailed: string;
    suggestions: {
      suggestion: string;
      prompt: string;
      icon: LucideIcon;
      menu?: "ppt";
    }[];
    pptRouteMenuTitle: string;
    pptRouteSuggestions: {
      id: string;
      suggestion: string;
      description: string;
      prompt: string;
      icon: LucideIcon;
    }[];
    suggestionsCreate: (
      | {
          suggestion: string;
          prompt: string;
          icon: LucideIcon;
        }
      | {
          type: "separator";
        }
    )[];
    pleaseWaitStreaming: string;
  };

  // Sidebar
  sidebar: {
    recentChats: string;
    newChat: string;
    chats: string;
    demoChats: string;
    agents: string;
    knowledge: string;
    skillCenter: string;
    openSkillCenter: string;
    scheduledTasks: string;
    agentsDisabledTooltip: string;
    channels: string;
  };

  // Scheduled tasks
  scheduledTasks: {
    scheduleType: { cron: string; once: string; interval: string };
    preset: {
      label: string;
      hourly: string;
      daily: string;
      weekly: string;
      monthly: string;
      custom: string;
    };
    fields: {
      minute: string;
      time: string;
      weekday: string;
      dayOfMonth: string;
      cron: string;
      cronPlaceholder: string;
      cronHint: string;
      runAt: string;
      invalidRunAt: string;
      timezone: string;
      timezoneShanghai: string;
      intervalAmount: string;
      intervalUnitSeconds: string;
      intervalUnitMinutes: string;
      intervalUnitHours: string;
      intervalMinHint: string;
    };
    weekdays: {
      mon: string;
      tue: string;
      wed: string;
      thu: string;
      fri: string;
      sat: string;
      sun: string;
    };
    preview: string;
    cronHelp: string;
    model: {
      label: string;
      placeholder: string;
      hint: string;
      unset: string;
    };
    create: {
      title: string;
      description: string;
      taskTitle: string;
      prompt: string;
      agent: string;
      leadAgent: string;
      submit: string;
      fillRequired: string;
      adviceTitle: string;
      adviceFresh: string;
      adviceReuse: string;
      adviceCron: string;
      adviceCronFormat: string;
      adviceCronExamples: string[];
      adviceCronExternal: string;
    };
    context: {
      fresh: string;
      reuse: string;
      threadIdPlaceholder: string;
      reuseNoticeTitle: string;
      reuseNoticeDescription: string;
    };
    search: { placeholder: string; clear: string; noResults: string };
    filters: {
      label: string;
      allStatuses: string;
      enabled: string;
      paused: string;
      completed: string;
      failed: string;
      allTypes: string;
      cron: string;
      once: string;
      interval: string;
    };
    detail: {
      contextMode: string;
      agent: string;
      thread: string;
      lastThread: string;
      schedule: string;
      expression: string;
      nextRun: string;
      lastRun: string;
      lastRunId: string;
      lastError: string;
      runsCount: string;
      runsCountOne: string;
      noRuns: string;
      noSelection: string;
      emptyTitle: string;
      emptyDescription: string;
      filteredByThread: string;
      loadFailed: string;
    };
    actions: {
      edit: string;
      cancelEdit: string;
      pause: string;
      resume: string;
      trigger: string;
      duplicate: string;
      duplicateTitleSuffix: string;
      delete: string;
    };
    deleteConfirm: string;
    errors: {
      create: string;
      update: string;
      pause: string;
      resume: string;
      trigger: string;
      delete: string;
    };
    edit: {
      titlePlaceholder: string;
      promptPlaceholder: string;
      submit: string;
    };
    status: {
      enabled: string;
      paused: string;
      running: string;
      completed: string;
      failed: string;
      cancelled: string;
    };
    runTrigger: { scheduled: string; manual: string };
    runStatus: {
      queued: string;
      launching: string;
      running: string;
      success: string;
      failed: string;
      skipped: string;
      interrupted: string;
    };
    history: {
      navigation: string;
      statusFilter: string;
      allStatuses: string;
      newer: string;
      older: string;
      latest: string;
      page: string;
      paused: string;
      loading: string;
      loadFailed: string;
      retry: string;
    };
    recipes: {
      label: string;
      trending: { title: string; desc: string };
      news: { title: string; desc: string };
      energyIntel: { title: string; desc: string };
      weekly: { title: string; desc: string };
    };
  };

  // Agents
  agents: {
    title: string;
    description: string;
    newAgent: string;
    emptyTitle: string;
    emptyDescription: string;
    featureDisabledTitle: string;
    featureDisabledDescription: string;
    myAgents: string;
    agentSquare: string;
    squareEmptyTitle: string;
    squareEmptyDescription: string;
    openExternal: string;
    publishToSquare: string;
    unpublishFromSquare: string;
    unpublishConfirm: string;
    publishSuccess: string;
    unpublishSuccess: string;
    chat: string;
    viewProfile: string;
    profileTitle: string;
    profileDescription: string;
    toolGroups: string;
    skills: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    newChat: string;
    createPageTitle: string;
    createPageSubtitle: string;
    nameStepTitle: string;
    nameStepHint: string;
    nameStepPlaceholder: string;
    avatarPickerLabel: string;
    avatarPickerHint: string;
    expandAvatars: string;
    collapseAvatars: string;
    nameStepContinue: string;
    nameStepInvalidError: string;
    nameStepAlreadyExistsError: string;
    nameStepNetworkError: string;
    nameStepCheckError: string;
    nameStepCheckErrorWithDetail: string;
    nameStepApiDisabledError: string;
    builderModelLabel: string;
    currentBuilderModel: string;
    changeBuilderModel: string;
    builderModelDialogTitle: string;
    nameStepBootstrapMessage: string;
    initialDescription: string;
    initialCategory: string;
    initialTags: string;
    save: string;
    saving: string;
    saveRequested: string;
    saveHint: string;
    saveCommandMessage: string;
    agentCreatedPendingRefresh: string;
    more: string;
    agentCreated: string;
    startChatting: string;
    backToGallery: string;
    personal: string;
    enterprise: string;
    noDescription: string;
    capabilities: string;
    edit: string;
    settingsTitle: string;
    settingsDescription: string;
    displayName: string;
    stableName: string;
    descriptionLabel: string;
    descriptionPlaceholder: string;
    category: string;
    tags: string;
    tagsPlaceholder: string;
    tagTooLong: string;
    tagDuplicate: string;
    editTag: string;
    deleteTag: string;
    tagInputLabel: string;
    tagLimitReached: string;
    addTag: string;
    confirmTagEdit: string;
    tagCount: string;
    autoTag: string;
    inferenceSettings: string;
    settingsModel: string;
    settingsModelDefault: string;
    settingsTemperature: string;
    settingsTemperatureHint: string;
    settingsMaxTokens: string;
    settingsMaxTokensPlaceholder: string;
    settingsThinking: string;
    settingsThinkingOn: string;
    settingsThinkingOff: string;
    settingsReasoningEffort: string;
    settingsInherit: string;
    settingsInvalidTemperature: string;
    settingsInvalidMaxTokens: string;
    updateSuccess: string;
    avatarUploadHint: string;
    uploadAvatar: string;
    avatarTooLarge: string;
    allCategories: string;
    businessDomainCount: string;
    expandCategories: string;
    collapseCategories: string;
    unifiedDomains: string;
    unifiedDomainsHint: string;
    distributedDomains: string;
    distributedDomainsHint: string;
    popularTags: string;
    expandTags: string;
    collapseTags: string;
    resetFilters: string;
    clearAll: string;
    noTags: string;
    searchPlaceholder: string;
    filters: string;
    filtersHint: string;
    resultCount: string;
    noResults: string;
    noResultsHint: string;
    loadMore: string;
    paginationNavigation: string;
    previousPage: string;
    nextPage: string;
    pageNumber: string;
    pageSummary: string;
  };

  // Breadcrumb
  breadcrumb: {
    workspace: string;
    chats: string;
    agents: string;
    knowledge: string;
    skillCenter: string;
    scheduledTasks: string;
  };

  // Workspace
  workspace: {
    settingsAndMore: string;
    about: string;
    logout: string;
    gatewayUnavailable: string;
    gatewayUnavailableRetrying: string;
  };

  // Conversation
  conversation: {
    noMessages: string;
    startConversation: string;
    branchCreated: string;
    branchFailed: string;
  };

  // Chats
  chats: {
    deleteChat: string;
    deleteConfirm: (title: string) => string;
    deleteFailed: string;
    noActiveChats: string;
    activeChats: string;
    archivedChats: string;
    archiveChat: string;
    restoreChat: string;
    archiveSuccess: string;
    restoreSuccess: string;
    archiveFailed: string;
    archiveDescription: string;
    undoArchive: string;
    noArchivedChats: string;
    noMatchingChats: string;
    loadChatsFailed: string;
    retryLoadChats: string;
    searchChats: string;
    loadMoreToSearch: string;
    loadingMore: string;
    loadOlderChats: string;
    pinChat: string;
    unpinChat: string;
    pinChatFailed: string;
  };

  // Sidecar
  sidecar: {
    title: string;
    open: string;
    close: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    deleteFailed: string;
    addToConversation: string;
    askInSideChat: string;
    reference: string;
    selectedTextFragment: string;
    selectedTextFragments: string;
    clearReferences: string;
    emptyTitle: string;
    emptyDescription: string;
    placeholder: string;
    send: string;
    sendFailed: string;
    noContext: string;
    continuing: string;
    selectionCrossesMessages: string;
  };

  // Channels
  channels: {
    title: string;
    connect: string;
    modify: string;
    reconnect: string;
    disconnect: string;
    connected: string;
    notConnected: string;
    pending: string;
    revoked: string;
    disabled: string;
    unconfigured: string;
    unavailable: string;
    unavailableShort: string;
    setupTitle: (name: string) => string;
    setupEditTitle: (name: string) => string;
    setupDescription: string;
    saveAndConnect: string;
    saveChanges: string;
    descriptions: Record<string, string>;
    connectedAs: (name: string) => string;
  };

  // Page titles (document title)
  pages: {
    appName: string;
    chats: string;
    newChat: string;
    untitled: string;
  };

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => string;
    lessSteps: string;
    executeCommand: string;
    presentFiles: string;
    needYourHelp: string;
    useTool: (toolName: string) => string;
    searchForRelatedInfo: string;
    searchForRelatedImages: string;
    searchFor: (query: string) => string;
    searchForRelatedImagesFor: (query: string) => string;
    searchOnWebFor: (query: string) => string;
    viewWebPage: string;
    listFolder: string;
    readFile: string;
    writeFile: string;
    clickToViewContent: string;
    writeTodos: string;
    skillInstallTooltip: string;
  };

  humanInput: {
    answered: string;
    pending: string;
    readOnly: string;
    otherLabel: string;
    otherPlaceholder: string;
    submit: string;
    emptyError: string;
    answeredValue: (value: string) => string;
  };

  // Uploads
  uploads: {
    uploading: string;
    uploadingFiles: string;
    limitsHint: (
      maxFiles: number,
      maxFileSize: string,
      maxTotalSize: string,
    ) => string;
    filesTooLarge: (files: string, maxFileSize: string) => string;
    tooManyFiles: (count: number, maxFiles: number) => string;
    totalSizeTooLarge: (count: number, maxTotalSize: string) => string;
  };

  // Subtasks
  subtasks: {
    subtask: string;
    executing: (count: number) => string;
    in_progress: string;
    completed: string;
    failed: string;
    incomplete: string;
    viewStepDetails: string;
    hideStepDetails: string;
    toolInput: string;
    toolOutput: string;
  };

  // Token Usage
  tokenUsage: {
    title: string;
    label: string;
    input: string;
    output: string;
    total: string;
    view: string;
    unavailable: string;
    unavailableShort: string;
    collecting: string;
    note: string;
    presets: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    presetDescriptions: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    finalAnswer: string;
    stepTotal: string;
    sharedAttribution: string;
    subagent: (description: string) => string;
    startTodo: (content: string) => string;
    completeTodo: (content: string) => string;
    updateTodo: (content: string) => string;
    removeTodo: (content: string) => string;
  };

  // Shortcuts
  shortcuts: {
    searchActions: string;
    noResults: string;
    actions: string;
    keyboardShortcuts: string;
    keyboardShortcutsDescription: string;
    openCommandPalette: string;
    toggleSidebar: string;
  };

  // Settings
  settings: {
    title: string;
    description: string;
    sections: {
      account: string;
      appearance: string;
      channels: string;
      memory: string;
      tools: string;
      skills: string;
      notification: string;
      about: string;
    };
    memory: {
      title: string;
      description: string;
      updateBehaviorHint: string;
      empty: string;
      rawJson: string;
      exportButton: string;
      exportSuccess: string;
      importButton: string;
      importConfirmTitle: string;
      importConfirmDescription: string;
      importFileLabel: string;
      importInvalidFile: string;
      importSuccess: string;
      manualFactSource: string;
      addFact: string;
      addFactTitle: string;
      editFactTitle: string;
      addFactSuccess: string;
      editFactSuccess: string;
      clearAll: string;
      clearAllConfirmTitle: string;
      clearAllConfirmDescription: string;
      clearAllSuccess: string;
      factDeleteConfirmTitle: string;
      factDeleteConfirmDescription: string;
      factDeleteSuccess: string;
      factContentLabel: string;
      factCategoryLabel: string;
      factConfidenceLabel: string;
      factContentPlaceholder: string;
      factCategoryPlaceholder: string;
      factConfidenceHint: string;
      factSave: string;
      factValidationContent: string;
      factValidationConfidence: string;
      noFacts: string;
      summaryReadOnly: string;
      memoryFullyEmpty: string;
      factPreviewLabel: string;
      searchPlaceholder: string;
      filterAll: string;
      filterFacts: string;
      filterSummaries: string;
      noMatches: string;
      markdown: {
        overview: string;
        userContext: string;
        work: string;
        personal: string;
        topOfMind: string;
        historyBackground: string;
        recentMonths: string;
        earlierContext: string;
        longTermBackground: string;
        updatedAt: string;
        facts: string;
        empty: string;
        table: {
          category: string;
          confidence: string;
          confidenceLevel: {
            veryHigh: string;
            high: string;
            normal: string;
            unknown: string;
          };
          content: string;
          source: string;
          createdAt: string;
          view: string;
        };
      };
    };
    appearance: {
      themeTitle: string;
      themeDescription: string;
      system: string;
      light: string;
      dark: string;
      systemDescription: string;
      lightDescription: string;
      darkDescription: string;
      languageTitle: string;
      languageDescription: string;
    };
    tools: {
      title: string;
      description: string;
      adminRequired: string;
      empty: string;
    };
    channels: {
      title: string;
      description: string;
      disabled: string;
    };
    skills: {
      title: string;
      description: string;
      publicReadOnly: string;
      createSkill: string;
      emptyTitle: string;
      emptyDescription: string;
      emptyButton: string;
      adminRequired: string;
      installAdminRequired: string;
    };
    notification: {
      title: string;
      description: string;
      requestPermission: string;
      deniedHint: string;
      testButton: string;
      testTitle: string;
      testBody: string;
      notSupported: string;
      disableNotification: string;
    };
    account: {
      profileTitle: string;
      email: string;
      role: string;
      changePasswordTitle: string;
      changePasswordDescription: string;
      ssoProvider: string;
      ssoPasswordDescription: string;
      ssoPasswordMessage: string;
      currentPassword: string;
      newPassword: string;
      confirmNewPassword: string;
      passwordMismatch: string;
      passwordTooShort: string;
      passwordChangedSuccess: string;
      networkError: string;
      updating: string;
      updatePassword: string;
      signOut: string;
    };
    acknowledge: {
      emptyTitle: string;
      emptyDescription: string;
    };
  };

  // Login / Auth
  login: {
    signInTitle: string;
    createAccountTitle: string;
    email: string;
    emailPlaceholder: string;
    password: string;
    passwordPlaceholder: string;
    pleaseWait: string;
    signIn: string;
    createAccount: string;
    createAdminAccount: string;
    adminSetupRequiredTitle: string;
    adminSetupRequiredDescription: string;
    orContinueWith: string;
    ssoHint: string;
    continueWith: (provider: string) => string;
    noAccountSignUp: string;
    haveAccountSignIn: string;
    backToHome: string;
    networkError: string;
    authFailed: string;
    signingIn: string;
    redirecting: string;
    authFailedRedirecting: string;
    errors: {
      sso_failed: string;
      sso_cancelled: string;
      sso_account_exists: string;
      sso_not_allowed: string;
    };
  };
}
