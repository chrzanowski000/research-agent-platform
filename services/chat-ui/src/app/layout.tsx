import type { Metadata } from "next";
import "./globals.css";
import { Inter } from "next/font/google";
import React from "react";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import Link from "next/link";

const inter = Inter({
  subsets: ["latin"],
  preload: true,
  display: "swap",
});

export const metadata: Metadata = {
  title: "Agent Chat",
  description: "Agent Chat UX by LangChain",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <nav className="border-b px-4 py-2 flex gap-4 text-sm font-medium">
          <Link href="/" className="hover:underline">Chat</Link>
          <Link href="/runs" className="hover:underline">Research Runs</Link>
        </nav>
        <NuqsAdapter>{children}</NuqsAdapter>
      </body>
    </html>
  );
}
