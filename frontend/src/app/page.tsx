import { Header } from "@/components/landing/header";
import { Hero } from "@/components/landing/hero";

export default function LandingPage() {
  return (
    <div className="min-h-svh w-full overflow-x-clip bg-slate-50">
      <Header />
      <main className="flex w-full flex-col">
        <Hero />
      </main>
    </div>
  );
}
