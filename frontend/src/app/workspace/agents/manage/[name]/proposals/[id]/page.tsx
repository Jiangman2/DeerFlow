import { EmployeeChangeReview } from "@/components/workspace/agents/employee-change-review";

export default async function EmployeeProposalPage({ params }: { params: Promise<{ name: string; id: string }> }) {
  const { name, id } = await params;
  return <EmployeeChangeReview name={name} id={id} />;
}
