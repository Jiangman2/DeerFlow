import { redirect } from "next/navigation";

export const metadata = {
  title: "Blog",
  filePath: "blog/index.mdx",
};

export default function PostsPage() {
  redirect("/");
}
