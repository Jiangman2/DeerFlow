import { redirect } from "next/navigation";

export function generateStaticParams() {
  return [];
}

export async function generateMetadata(props) {
  const params = await props.params;
  return {
    title: params.lang === "zh" ? "文档" : "Docs",
  };
}

export default async function Page(props) {
  await props.params;
  redirect("/");
}
