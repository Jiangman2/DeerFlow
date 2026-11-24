import { notFound } from "next/navigation";

import { ChatPreview } from "./preview";
export default function PreviewPage() {
  if (process.env.NODE_ENV !== "development") notFound();
  return <ChatPreview />;
}
