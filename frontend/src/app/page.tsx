import { Hero } from "@/components/landing/hero";

export default function LandingPage() {
  return (
    <div className="min-h-screen w-full bg-white text-slate-900">
      <main className="flex w-full flex-col overflow-hidden">
        <Hero />
      </main>
    </div>
  );
}
