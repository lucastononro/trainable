import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import { ToastProvider } from '@/components/Toast';
import { AppProvider } from '@/lib/AppContext';
import './globals.css';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'Trainable',
  description: 'LLM-powered ML experimentation platform',
  icons: {
    icon: '/logo-brain.png',
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <ToastProvider>
          <AppProvider>
            <div className="min-h-screen bg-black">
              <a
                href="#main-content"
                className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50 focus:bg-primary-600 focus:text-white focus:px-4 focus:py-2 focus:rounded-lg focus:shadow-lg transition-all"
              >
                Skip to main content
              </a>
              {children}
            </div>
          </AppProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
