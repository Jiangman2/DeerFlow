import { redirect } from "next/navigation";

export async function generateMetadata(props) {
  const params = await props.params;
  return {
    title: params.tag,
    filePath: "blog/index.mdx",
  };
}

export default async function TagPage(props) {
  await props.params;
  redirect("/");
}
