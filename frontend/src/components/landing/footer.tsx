import { useMemo } from "react";

import { brand } from "@/brand";

export function Footer() {
  const year = useMemo(() => new Date().getFullYear(), []);
  return (
    <footer className="container-md mx-auto mt-32 flex flex-col items-center justify-center">
      <hr className="m-0 h-px w-full border-none bg-border" />
      <div className="text-muted-foreground container flex h-20 flex-col items-center justify-center text-sm">
        <p className="text-center font-serif text-lg md:text-xl">
          &quot;Originated from Open Source, give back to Open Source.&quot;
        </p>
      </div>
      <div className="text-muted-foreground container mb-8 flex flex-col items-center justify-center text-xs">
        <p>Licensed under MIT License</p>
        <p>&copy; {year} {brand.productName}</p>
      </div>
    </footer>
  );
}
