import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import Providers from "./providers";
import Shell from "@/components/Shell";

export const metadata: Metadata = {
  title: "OSINT Memory — evidence-first intelligence workspace",
  description: "Persistent OSINT knowledge graph with provenance",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
