"use client";

import React from "react";
import { useQueryState } from "nuqs";
import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { constructOpenInStudioURL } from "@/components/thread/agent-inbox/utils";
import { useStreamContext } from "@/providers/Stream";
import { useState, useEffect, useRef, useCallback } from "react";
import { validate } from "uuid";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface GraphNode {
  id: string;
}

interface GraphEdge {
  source: string;
  target: string;
  conditional?: boolean;
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Ordered node IDs (topological, excluding __start__/__end__) */
  order: string[];
  /** Back-edges that form loops (target comes before source in order) */
  backEdges: GraphEdge[];
}

type NodeState = "pending" | "active" | "done";

// ---------------------------------------------------------------------------
// Color palette – assigned by position, with overrides for known nodes
// ---------------------------------------------------------------------------

const NODE_COLOR_OVERRIDES: Record<string, { border: string; bg: string; text: string }> = {
  search_decision: { border: "border-rose-300",    bg: "bg-rose-50",    text: "text-rose-700"    },
  web_search:      { border: "border-purple-300",  bg: "bg-purple-50",  text: "text-purple-700"  },
  generate:        { border: "border-cyan-300",     bg: "bg-cyan-50",    text: "text-cyan-700"    },
  reflect:         { border: "border-emerald-300",  bg: "bg-emerald-50", text: "text-emerald-700" },
};

const COLOR_PALETTE = [
  { border: "border-rose-300",    bg: "bg-rose-50",    text: "text-rose-700"    },
  { border: "border-purple-300",  bg: "bg-purple-50",  text: "text-purple-700"  },
  { border: "border-cyan-300",    bg: "bg-cyan-50",    text: "text-cyan-700"    },
  { border: "border-emerald-300", bg: "bg-emerald-50", text: "text-emerald-700" },
  { border: "border-amber-300",   bg: "bg-amber-50",   text: "text-amber-700"   },
  { border: "border-blue-300",    bg: "bg-blue-50",    text: "text-blue-700"    },
  { border: "border-pink-300",    bg: "bg-pink-50",    text: "text-pink-700"    },
  { border: "border-teal-300",    bg: "bg-teal-50",    text: "text-teal-700"    },
];

function getNodeColor(name: string, index: number) {
  return NODE_COLOR_OVERRIDES[name] ?? COLOR_PALETTE[index % COLOR_PALETTE.length];
}

const PENDING_STYLE = "border-gray-200 bg-gray-50 text-gray-400";

// ---------------------------------------------------------------------------
// Parse graph API response
// ---------------------------------------------------------------------------

function parseGraphResponse(raw: {
  nodes: Array<{ id: string; data?: unknown }>;
  edges: Array<{ source: string; target: string; conditional?: boolean; data?: unknown }>;
}): GraphData {
  // Filter out __start__ and __end__ meta-nodes
  const realNodes = raw.nodes
    .filter((n) => n.id !== "__start__" && n.id !== "__end__")
    .map((n) => ({ id: n.id }));

  const realEdges = raw.edges.map((e) => ({
    source: e.source,
    target: e.target,
    conditional: e.conditional ?? false,
  }));

  // Build adjacency for topological sort (only forward edges between real nodes)
  const nodeSet = new Set(realNodes.map((n) => n.id));

  // Find entry node: target of __start__ edge
  const entryEdge = raw.edges.find((e) => e.source === "__start__");
  const entryNode = entryEdge?.target;

  // Topological sort via BFS from entry
  const adj: Record<string, string[]> = {};
  for (const n of realNodes) adj[n.id] = [];
  for (const e of realEdges) {
    if (nodeSet.has(e.source) && nodeSet.has(e.target)) {
      adj[e.source] = adj[e.source] ?? [];
      adj[e.source].push(e.target);
    }
  }

  // BFS-based ordering starting from entry
  const order: string[] = [];
  const visited = new Set<string>();
  const queue: string[] = entryNode && nodeSet.has(entryNode) ? [entryNode] : [...nodeSet];

  // Simple BFS that avoids revisits
  while (queue.length > 0) {
    const node = queue.shift()!;
    if (visited.has(node)) continue;
    visited.add(node);
    order.push(node);
    for (const next of adj[node] ?? []) {
      if (!visited.has(next)) queue.push(next);
    }
  }
  // Add any unreachable nodes
  for (const n of realNodes) {
    if (!visited.has(n.id)) order.push(n.id);
  }

  // Identify back-edges (target comes before source in order)
  const orderIndex: Record<string, number> = {};
  order.forEach((id, i) => { orderIndex[id] = i; });

  const backEdges: GraphEdge[] = [];
  for (const e of realEdges) {
    if (
      nodeSet.has(e.source) &&
      nodeSet.has(e.target) &&
      (orderIndex[e.target] ?? 0) < (orderIndex[e.source] ?? 0)
    ) {
      backEdges.push(e);
    }
  }

  return { nodes: realNodes, edges: realEdges, order, backEdges };
}

// ---------------------------------------------------------------------------
// Hook: fetch graph topology
// ---------------------------------------------------------------------------

function useGraphData(assistantId: string | null): GraphData | null {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const fetchedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!assistantId) return;
    if (fetchedRef.current === assistantId) return;
    fetchedRef.current = assistantId;

    async function load() {
      // Step 1: resolve graph_id → assistant UUID (if not already a UUID)
      let uuid = assistantId!;
      if (!validate(uuid)) {
        const searchRes = await fetch("/api/assistants/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ graph_id: assistantId, limit: 1 }),
        });
        if (!searchRes.ok) return;
        const assistants = await searchRes.json();
        if (!Array.isArray(assistants) || assistants.length === 0) return;
        uuid = assistants[0].assistant_id;
      }

      // Step 2: fetch graph topology via Next.js proxy (avoids CORS)
      const graphRes = await fetch(`/api/assistants/${uuid}/graph`);
      if (!graphRes.ok) return;
      const data = await graphRes.json();
      if (data?.nodes && data?.edges) {
        setGraphData(parseGraphResponse(data));
      }
    }

    load().catch(() => {});
  }, [assistantId]);

  return graphData;
}

// ---------------------------------------------------------------------------
// UI components
// ---------------------------------------------------------------------------

function NodeBadge({ name, state, colorIndex }: { name: string; state: NodeState; colorIndex: number }) {
  const colors = getNodeColor(name, colorIndex);
  const activeClass = cn(colors.border, colors.bg, colors.text);
  return (
    <span
      className={cn(
        "rounded-md border px-2 py-0.5 font-mono text-xs transition-colors duration-300 whitespace-nowrap",
        state === "pending" ? PENDING_STYLE : activeClass,
      )}
    >
      {name}
    </span>
  );
}

function Arrow({ active = true }: { active?: boolean }) {
  return (
    <span
      className={cn(
        "text-xs transition-colors duration-300 shrink-0",
        active ? "text-gray-400" : "text-gray-200",
      )}
    >
      →
    </span>
  );
}

// ---------------------------------------------------------------------------
// LoopRow – ref-based measurement for automatic connector alignment
// ---------------------------------------------------------------------------

function LoopRow({
  children,
  count,
  active,
}: {
  children?: React.ReactNode;
  count: number;
  active: boolean;
}) {
  const color     = active ? "#9ca3af" : "#e5e7eb";
  const textColor = active ? "#6b7280" : "#d1d5db";

  const legH   = 14;
  const arrowW = 8;
  const arrowH = 5;
  const thick  = 1.5;
  const totalH = legH + arrowH + 2;

  const rowRef = useRef<HTMLDivElement>(null);
  const [insets, setInsets] = useState<{ left: number; right: number } | null>(null);

  const measure = useCallback(() => {
    const row = rowRef.current;
    if (!row) return;
    const badges = row.querySelectorAll("[data-node-badge]");
    if (badges.length < 2) return;
    const first = badges[0] as HTMLElement;
    const last = badges[badges.length - 1] as HTMLElement;
    const rowRect = row.getBoundingClientRect();
    const firstCenter = first.getBoundingClientRect().left + first.getBoundingClientRect().width / 2 - rowRect.left;
    const lastCenter = last.getBoundingClientRect().left + last.getBoundingClientRect().width / 2 - rowRect.left;
    setInsets({ left: firstCenter, right: rowRect.width - lastCenter });
  }, []);

  useEffect(() => {
    measure();
    // Re-measure on resize
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure, children]);

  return (
    <div style={{ position: "relative", paddingTop: insets ? totalH : 0, display: "inline-flex", flexDirection: "column" }}>
      {insets && (
        <>
          {/* Left vertical leg + arrowhead */}
          <div style={{
            position: "absolute",
            top: 0,
            left: insets.left,
            transform: "translateX(-50%)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
          }}>
            <div style={{ width: thick, height: legH, background: color }} />
            {/* @ts-ignore */}
            <svg width={arrowW} height={arrowH} viewBox={`0 0 ${arrowW} ${arrowH}`} style={{ display: "block", flexShrink: 0 }}>
              {/* @ts-ignore */}
              <path
                d={`M 0 0 L ${arrowW / 2} ${arrowH} L ${arrowW} 0`}
                stroke={color}
                strokeWidth="1.5"
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>

          {/* Horizontal bar */}
          <div style={{
            position: "absolute",
            top: 0,
            left: insets.left,
            right: insets.right,
            height: thick,
            background: color,
          }}>
            <div style={{
              position: "absolute",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              background: "#f9fafb",
              border: `1px solid ${active ? "#e5e7eb" : "#f3f4f6"}`,
              borderRadius: 999,
              padding: "1px 7px",
              fontSize: 10,
              fontFamily: "ui-monospace, monospace",
              fontWeight: 600,
              color: textColor,
              whiteSpace: "nowrap" as const,
              lineHeight: "14px",
              letterSpacing: "0.2px",
            }}>
              ×{count}
            </div>
          </div>

          {/* Right vertical leg */}
          <div style={{
            position: "absolute",
            top: 0,
            right: insets.right,
            transform: "translateX(50%)",
            width: thick,
            height: totalH,
            background: color,
          }} />
        </>
      )}

      {/* Node row */}
      <div ref={rowRef} className="flex items-center gap-1">
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dynamic execution graph
// ---------------------------------------------------------------------------

function DynamicExecutionGraph({
  graphData,
  values,
}: {
  graphData: GraphData;
  values: Record<string, unknown>;
}) {
  const { order, backEdges } = graphData;
  const iteration = (values?.iteration as number) ?? 0;
  const done = (values?.done as boolean) ?? false;
  const blocked = (values?.blocked as boolean) ?? false;
  const webSearchCount = (values?.web_search_count as number) ?? 0;
  const blockReason = (values?.block_reason as string) ?? "";

  // Determine node states generically
  const nodeStates: Record<string, NodeState> = {};
  for (let i = 0; i < order.length; i++) {
    const id = order[i];
    if (done || blocked) {
      nodeStates[id] = "done";
    } else if (iteration === 0) {
      nodeStates[id] = "pending";
    } else {
      // During execution: nodes earlier in the order are "done",
      // the last one or two are "active"
      // Heuristic: with N nodes and iteration I, we estimate progress
      const progress = Math.min(iteration / Math.max((values?.max_iterations as number) ?? 3, 1), 1);
      const activeIdx = Math.floor(progress * (order.length - 1));
      if (i < activeIdx) {
        nodeStates[id] = "done";
      } else if (i === activeIdx) {
        nodeStates[id] = "active";
      } else {
        nodeStates[id] = "pending";
      }
    }
  }

  // If there's at least one iteration, mark all nodes as at least visited
  if (iteration > 0) {
    for (const id of order) {
      if (nodeStates[id] === "pending") {
        nodeStates[id] = "active";
      }
    }
  }

  const hasLoop = backEdges.length > 0;
  const anyActive = Object.values(nodeStates).some((s) => s !== "pending");

  const nodeRow = order.map((id, i) => (
    <span key={id} className="contents">
      {i > 0 && <Arrow active={nodeStates[order[i - 1]] !== "pending"} />}
      <span data-node-badge>
        <NodeBadge name={id} state={nodeStates[id]} colorIndex={i} />
      </span>
    </span>
  ));

  return (
    <div className="mt-3">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-gray-400">
        Execution path
      </p>
      {hasLoop ? (
        <LoopRow count={iteration} active={anyActive}>
          {nodeRow}
        </LoopRow>
      ) : (
        <div className="inline-flex items-center gap-1">
          {nodeRow}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-3 text-xs text-gray-500">
        <span>
          Iterations: <strong className="text-gray-700">{iteration}</strong>
        </span>
        {webSearchCount > 0 && (
          <span>
            Web searches: <strong className="text-gray-700">{webSearchCount}</strong>
          </span>
        )}
        {blocked && (
          <span className="text-amber-600">
            Blocked: {blockReason || "safety"}
          </span>
        )}
        {done && !blocked && (
          <span className="text-green-600">Completed</span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fallback stats (when graph API unavailable)
// ---------------------------------------------------------------------------

function FallbackStats({
  assistantId,
  values,
}: {
  assistantId: string;
  values: Record<string, unknown>;
}) {
  const iteration = (values?.iteration as number) ?? 0;
  const webSearchCount = (values?.web_search_count as number) ?? 0;
  const done = (values?.done as boolean) ?? false;
  const blocked = (values?.blocked as boolean) ?? false;
  const blockReason = (values?.block_reason as string) ?? "";

  return (
    <div className="mt-3 space-y-1 text-xs text-gray-500">
      <div>Agent: <span className="font-mono text-gray-700">{assistantId}</span></div>
      <div>Iterations: <strong className="text-gray-700">{iteration}</strong></div>
      {webSearchCount > 0 && (
        <div>Web searches: <strong className="text-gray-700">{webSearchCount}</strong></div>
      )}
      {done && <div className="text-green-600">Completed</div>}
      {blocked && (
        <div className="text-amber-600">Blocked: {blockReason || "safety"}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main exports
// ---------------------------------------------------------------------------

export function TracePanel() {
  const [apiUrl] = useQueryState("apiUrl");
  const [threadId] = useQueryState("threadId");
  const [assistantId] = useQueryState("assistantId");
  const thread = useStreamContext();
  const graphData = useGraphData(assistantId);

  if (!assistantId) return null;

  const values = thread.values as Record<string, unknown>;

  const langSmithUrl =
    apiUrl && threadId ? constructOpenInStudioURL(apiUrl, threadId) : null;

  return (
    <div className="w-full">
      <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
        {langSmithUrl && (
          <a
            href={langSmithUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 hover:underline"
          >
            <ExternalLink className="size-3" />
            View in LangSmith
          </a>
        )}

        {graphData ? (
          <DynamicExecutionGraph graphData={graphData} values={values} />
        ) : (
          <FallbackStats assistantId={assistantId} values={values} />
        )}
      </div>
    </div>
  );
}

export function useHasTraceData(): boolean {
  const [threadId] = useQueryState("threadId");
  const [assistantId] = useQueryState("assistantId");
  const thread = useStreamContext();
  const values = thread.values as Record<string, unknown>;
  const iteration = (values?.iteration as number) ?? 0;
  const webSearchCount = (values?.web_search_count as number) ?? 0;
  if (!threadId || !assistantId) return false;
  return iteration > 0 || webSearchCount > 0;
}
