import type { SVGProps } from "react";

import { cn } from "@/lib/utils";

type BrandMarkProps = SVGProps<SVGSVGElement> & {
  title?: string;
};

/**
 * The protected boundary and linked nodes represent source-aware change control.
 */
export function BrandMark({ className, title, ...props }: BrandMarkProps) {
  return (
    <svg
      aria-hidden={title ? undefined : true}
      aria-label={title}
      className={cn("block shrink-0", className)}
      fill="none"
      role={title ? "img" : undefined}
      viewBox="0 0 40 40"
      xmlns="http://www.w3.org/2000/svg"
      {...props}
    >
      {title ? <title>{title}</title> : null}
      <path
        className="fill-current"
        d="M20 2.75 34 7.6v10.74c0 8.84-5.66 16.16-14 18.91C11.66 34.5 6 27.18 6 18.34V7.6L20 2.75Z"
      />
      <path
        className="stroke-primary-foreground"
        d="m13.25 15.75 6.75 4.5 6.75-4.5M20 20.25v7"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <circle className="fill-primary-foreground" cx="13.25" cy="15.75" r="2.25" />
      <circle className="fill-primary-foreground" cx="26.75" cy="15.75" r="2.25" />
      <circle className="fill-primary-foreground" cx="20" cy="20.25" r="2.25" />
      <circle className="fill-primary-foreground" cx="20" cy="27.25" r="2.25" />
    </svg>
  );
}
