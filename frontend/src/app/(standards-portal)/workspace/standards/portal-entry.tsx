"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { StandardsModuleKey } from "@/core/standards-portal/config";

import styles from "./standards.module.css";

export function StandardsPortalEntry({
  moduleKey,
  href,
  title,
  className,
  unconfiguredMessage,
  closeLabel,
  children,
}: Readonly<{
  moduleKey: StandardsModuleKey;
  href?: string;
  title: string;
  className?: string;
  unconfiguredMessage: string;
  closeLabel: string;
  children: React.ReactNode;
}>) {
  if (href) {
    return (
      <Link
        className={className}
        data-module={moduleKey}
        href={href}
        prefetch={false}
      >
        {children}
      </Link>
    );
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <button className={className} data-module={moduleKey} type="button">
          {children}
        </button>
      </DialogTrigger>
      <DialogContent className={styles.dialog} showCloseButton={false}>
        <DialogHeader>
          <DialogTitle className={styles.dialogTitle}>{title}</DialogTitle>
          <DialogDescription className={styles.dialogDescription}>
            {unconfiguredMessage}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button className={styles.closeButton} type="button">
              {closeLabel}
            </Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
