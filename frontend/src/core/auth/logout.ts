import { updateLocalSettings } from "../settings/store";

export function resetLocalSettingsForLogout() {
  try {
    updateLocalSettings("context", { datalake_enabled: false });
  } catch {
    // Browser policy or storage quotas must not prevent server-side logout.
  }
}
