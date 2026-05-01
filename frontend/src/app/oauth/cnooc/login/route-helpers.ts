const SUCCESS_REDIRECT_PATH = "/workspace/chats/new";

export function buildCnoocLoginRedirectUrl(requestUrl: string) {
  const configuredBaseUrl =
    process.env.BETTER_AUTH_URL ?? process.env.BETTER_AUTH_BASE_URL;
  if (configuredBaseUrl) {
    return new URL(SUCCESS_REDIRECT_PATH, configuredBaseUrl);
  }
  return new URL(SUCCESS_REDIRECT_PATH, requestUrl);
}
