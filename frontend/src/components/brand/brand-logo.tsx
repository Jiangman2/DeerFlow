import Image from "next/image";

import { brand } from "@/brand";
import { cn } from "@/lib/utils";

export function BrandLogo({
  variant = "full",
  className,
  priority,
}: {
  variant?: "mark" | "full";
  className?: string;
  priority?: boolean;
}) {
  const src = variant === "mark" ? brand.logo.markSrc : brand.logo.fullSrc;
  const size =
    variant === "mark"
      ? { width: 1280, height: 1169 }
      : { width: 1928, height: 578 };
  return (
    <Image
      src={src}
      alt={brand.productName}
      width={size.width}
      height={size.height}
      priority={priority}
      className={cn("select-none", className)}
    />
  );
}
