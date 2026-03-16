"use client";

import { useState, useEffect, useRef } from "react";
import { useQueryState } from "nuqs";
import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";

export interface PresetPrompt {
  label: string;
  prompt: string;
}

export const AGENT_PROMPTS: Record<string, PresetPrompt[]> = {
  self_reflection_agent: [
    {
      label: "Reflect on a decision",
      prompt:
        "Help me reflect on a recent decision I made at work and identify what I could have done differently.",
    },
    {
      label: "Weekly review",
      prompt:
        "Guide me through a weekly review: what went well, what didn't, and what I should focus on next week.",
    },
    {
      label: "Identify blind spots",
      prompt:
        "What are common blind spots people have when reflecting on their own performance? Help me check for them.",
    },
    {
      label: "Reframe a failure",
      prompt:
        "I recently experienced a failure. Help me reframe it as a learning opportunity using structured reflection.",
    },
    {
      label: "Goal alignment check",
      prompt:
        "Help me check whether my current daily activities are aligned with my longer-term goals.",
    },
  ],
  self_reflection_agent_v2: [
    {
      label: "Deep dive reflection",
      prompt:
        "Run a deep reflection session: identify assumptions, emotions, and alternative perspectives.",
    },
    {
      label: "Pattern recognition",
      prompt:
        "Help me identify recurring patterns in my thinking or behavior that might be holding me back.",
    },
    {
      label: "Values clarification",
      prompt:
        "Guide me through a values clarification exercise to understand what matters most to me right now.",
    },
    {
      label: "Conflict reflection",
      prompt:
        "I had a conflict with someone recently. Help me reflect on it from multiple perspectives.",
    },
    {
      label: "Growth edge",
      prompt:
        "What is my current growth edge? Help me identify where I'm being challenged to develop right now.",
    },
  ],
};

interface PresetPromptsProps {
  onSelect: (prompt: string) => void;
}

export function PresetPrompts({ onSelect }: PresetPromptsProps) {
  const [assistantId] = useQueryState("assistantId");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const firstKey = Object.keys(AGENT_PROMPTS)[0];
  const resolvedId =
    assistantId && assistantId in AGENT_PROMPTS ? assistantId : firstKey;
  const prompts = resolvedId ? AGENT_PROMPTS[resolvedId] : null;

  if (!prompts) return null;

  return (
    <div ref={containerRef} className="relative px-3.5 pt-3 pb-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-600 shadow-xs transition-colors hover:border-gray-300 hover:bg-gray-50 focus:outline-none",
          open && "border-gray-300 bg-gray-50",
        )}
      >
        Demo Prompts
        <ChevronDown
          className={cn(
            "size-3 text-gray-400 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="absolute bottom-full left-3.5 z-50 mb-1 w-80 rounded-xl border border-gray-200 bg-white p-2 shadow-md">
          <p className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-gray-400">
            Demo Prompts
          </p>
          {prompts.map((preset) => (
            <button
              key={preset.label}
              type="button"
              onClick={() => {
                onSelect(preset.prompt);
                setOpen(false);
              }}
              className="w-full rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
            >
              {preset.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
