import { EmployeeVersions } from "@/components/workspace/agents/employee-versions";

export default async function EmployeeManagementPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  return <EmployeeVersions name={name} />;
}
