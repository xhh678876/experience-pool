import NextLink from "next/link";
import type { ComponentProps, ReactNode } from "react";
export { withBase } from "@/lib/base-path";
import { withBase } from "@/lib/base-path";

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
