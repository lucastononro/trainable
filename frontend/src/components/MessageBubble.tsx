'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MessageBubbleProps {
  role: string;
  content: string;
}

export default function MessageBubble({ role, content }: MessageBubbleProps) {
  const isUser = role === 'user';

  if (isUser) {
    return (
      <div className="flex justify-end animate-slide-up">
        <div className="max-w-[75%] px-4 py-2.5 rounded-3xl rounded-br-lg bg-primary-600 text-white text-sm leading-relaxed">
          {content}
        </div>
      </div>
    );
  }

  return (
    <div className="animate-slide-up">
      <div className="max-w-[85%] text-sm leading-relaxed text-gray-200 prose-invert">
        <div className="markdown-chat">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
