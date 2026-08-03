import { redirect } from "next/navigation";

import { Header } from "@/components/landing/header";
import { Hero } from "@/components/landing/hero";
import { getServerSideUser } from "@/core/auth/server";
import { resolveUnauthenticatedHomeRedirect } from "@/core/auth/unauthenticated-home";
import { env } from "@/env";

export default async function LandingPage() {
  const authResult = await getServerSideUser();
  const unauthenticatedHome = resolveUnauthenticatedHomeRedirect(
    authResult,
    env.DEER_FLOW_UNAUTHENTICATED_HOME_URL,
  );
  if (unauthenticatedHome) {
    redirect(unauthenticatedHome);
  }

  return (
    <div className="min-h-svh w-full overflow-x-clip bg-slate-50">
      <Header />
      <main className="flex w-full flex-col">
        <Hero />
      </main>
    </div>
  );
}
