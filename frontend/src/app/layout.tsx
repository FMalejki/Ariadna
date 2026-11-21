import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import Providers from "./providers";
import Shell from "@/components/Shell";

export const metadata: Metadata = {
  title: { default: "Ariadna", template: "%s | Ariadna" },
  description: "Ariadna: trwały graf wiedzy OSINT, w którym każde powiązanie pokazuje swoje dowody.",
};

export const viewport: Viewport = { themeColor: "#e8ecee", colorScheme: "light" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="pl">
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
