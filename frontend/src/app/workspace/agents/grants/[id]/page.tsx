import { ResourceGrantDetail } from "@/components/workspace/agents/resource-grant-detail";

export default async function EmployeeResourceGrantPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ResourceGrantDetail id={id} />;
}
