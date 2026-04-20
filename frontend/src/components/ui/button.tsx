import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-[rgba(88,134,189,0.35)] aria-invalid:ring-[3px] aria-invalid:ring-[rgba(204,48,48,0.25)] aria-invalid:border-destructive",
  {
    variants: {
      variant: {
        default:
          "cursor-pointer bg-primary text-primary-foreground shadow-sm hover:brightness-95",
        destructive:
          "cursor-pointer bg-destructive text-white hover:brightness-95 focus-visible:ring-[rgba(204,48,48,0.25)] dark:bg-destructive",
        outline:
          "cursor-pointer border border-border bg-card text-foreground shadow-xs hover:brightness-95 dark:bg-card",
        secondary:
          "cursor-pointer bg-secondary text-secondary-foreground hover:brightness-95",
        ghost:
          "cursor-pointer hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent",
        link: "cursor-pointer text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        sm: "h-8 rounded-md gap-1.5 px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
        "icon-sm": "size-8",
        "icon-lg": "size-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
