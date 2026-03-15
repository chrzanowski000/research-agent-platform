import { NextResponse } from "next/server";

export async function GET() {
  // LANGGRAPH_API_URL is a server-only var (works in Docker with service names).
  // Fall back to NEXT_PUBLIC_API_URL, then localhost.
  const apiUrl =
    process.env.LANGGRAPH_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:2024";

  try {
    // Fetch all assistants, then deduplicate by graph_id
    const res = await fetch(`${apiUrl}/assistants/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 100, offset: 0 }),
    });
    if (!res.ok) {
      return NextResponse.json({ graphs: [] }, { status: 502 });
    }
    const data = await res.json();
    // data is an array of assistants, each has a graph_id
    const graphs: string[] = Array.isArray(data)
      ? [...new Set<string>(data.map((a: { graph_id: string }) => a.graph_id))]
      : [];
    return NextResponse.json({ graphs });
  } catch {
    return NextResponse.json({ graphs: [] }, { status: 502 });
  }
}
