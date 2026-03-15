import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const RESEARCH_PERSISTENCE_API =
  process.env.RESEARCH_PERSISTENCE_API_URL ?? "http://localhost:8001";

async function proxy(req: NextRequest, path: string[]): Promise<NextResponse> {
  const url = `${RESEARCH_PERSISTENCE_API}/research/${path.join("/")}${req.nextUrl.search}`;
  const headers = new Headers(req.headers);
  headers.delete("host");

  const hasBody = req.method !== "GET" && req.method !== "HEAD" && req.body !== null;
  if (!hasBody) {
    headers.delete("content-length");
    headers.delete("transfer-encoding");
    headers.delete("content-type");
  }

  const init: RequestInit = {
    method: req.method,
    headers,
  };
  if (hasBody) {
    init.body = req.body as BodyInit;
    (init as RequestInit & { duplex?: string }).duplex = "half";
  }

  try {
    const res = await fetch(url, init);
    const responseHeaders = new Headers(res.headers);
    const isBodylessStatus = res.status === 204 || res.status === 205 || res.status === 304;

    if (isBodylessStatus) {
      responseHeaders.delete("content-length");
      responseHeaders.delete("content-type");
      responseHeaders.delete("transfer-encoding");
      return new NextResponse(null, {
        status: res.status,
        headers: responseHeaders,
      });
    }

    const body = await res.arrayBuffer();
    return new NextResponse(body, {
      status: res.status,
      headers: responseHeaders,
    });
  } catch (err) {
    return NextResponse.json({ error: "Research API unreachable" }, { status: 502 });
  }
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path);
}
export async function POST(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path);
}
export async function PUT(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path);
}
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path);
}
export async function DELETE(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path);
}
export async function OPTIONS(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(req, (await params).path);
}
