import type { Metadata } from "next";
import Link from "next/link";
import { HeaderStats } from "@/components/layout/header-stats";
import { Sidebar } from "@/components/layout/sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Miao AI",
  description: "Self-hosted AI agent platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh" suppressHydrationWarning>
      <head>
        <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:ital,wght@0,400;0,500;0,600;1,400&display=swap"
          rel="stylesheet"
        />
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function () {
                try {
                  var key = "miao-ai-theme";
                  var theme = localStorage.getItem(key);
                  if (!theme) {
                    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
                  }
                  document.documentElement.dataset.theme = theme;
                } catch (e) {}
              })();
            `,
          }}
        />
      </head>
      <body className="font-sans antialiased">
        <div className="app-shell">
          <Sidebar />
          <div className="main-content">
            <header className="top-header">
              <div className="header-left">
                <div className="breadcrumb">
                <nav className="flex items-center gap-1.5">
                  <Link href="/" className="transition-colors hover:text-foreground">
                    Home
                  </Link>
                </nav>
                </div>
              </div>
              <HeaderStats />
            </header>
            <main className="page-content">
              {children}
            </main>
            <footer className="page-footer">
              <p>Miao AI — Self-hosted AI Agent Platform · Made with love for developers</p>
            </footer>
          </div>
        </div>
      </body>
    </html>
  );
}
