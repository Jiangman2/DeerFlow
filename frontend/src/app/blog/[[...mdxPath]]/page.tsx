import { redirect } from "next/navigation";

export async function generateMetadata(props) {
  await props.params;
  return {
    title: "Blog",
  };
}

export default async function BlogPage(props) {
  await props.params;
  redirect("/");
}
