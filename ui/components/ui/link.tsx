import NextLink from "next/link";
import type { ComponentProps, ReactNode } from "react";

const BASE = (process.env.NEXT_PUBLIC_UI_BASE ?? "").replace(/\/$/, "");

export function withBase(path: string): string {
  if (!BASE) return path;
  if (!path.startsWith("/")) return path;
  if (path.startsWith(BASE + "/") || path === BASE) return path;
  return BASE + path;
}

type Props = Omit<ComponentProps<typeof NextLink>, "href"> & {
  href: string;
  children?: ReactNode;
};

export default function Link({ href, children, ...rest }: Props) {
  return (
    <NextLink href={withBase(href)} {...rest}>
      {children}
    </NextLink>
  );
}
