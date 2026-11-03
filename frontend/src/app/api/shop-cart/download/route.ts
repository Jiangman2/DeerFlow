import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { Readable } from "node:stream";

import { NextResponse, type NextRequest } from "next/server";

import { env } from "@/env";
import {
  authorizeCnoocPlatformToken,
  buildCnoocPlatformTokenRejection,
} from "@/server/cnooc-auth/platform-token";

const DATA_LAKE_DOWNLOAD_BASE =
  "https://datalake.cnooc/jsearch/bd_search/external-download/api/cart/download";

function buildDownloadHeaders(token: string | null) {
  const headers = new Headers();
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }
  return headers;
}

function requestDataLakeDownload(
  url: URL,
  token: string | null,
): Promise<Response> {
  return new Promise((resolve, reject) => {
    const request = url.protocol === "https:" ? httpsRequest : httpRequest;
    const upstreamRequest = request(
      url,
      {
        headers: Object.fromEntries(buildDownloadHeaders(token)),
        rejectUnauthorized: env.SHOP_CART_DOWNLOAD_TLS_VERIFY !== "false",
      },
      (upstreamResponse) => {
        const headers = new Headers();
        for (const [name, value] of Object.entries(upstreamResponse.headers)) {
          if (Array.isArray(value)) {
            headers.set(name, value.join(", "));
          } else if (value) {
            headers.set(name, value);
          }
        }

        const body = Readable.toWeb(upstreamResponse) as unknown as BodyInit;
        resolve(
          new Response(body, {
            status: upstreamResponse.statusCode ?? 502,
            headers,
          }),
        );
      },
    );

    upstreamRequest.on("error", reject);
    upstreamRequest.end();
  });
}

export async function GET(request: NextRequest) {
  const fileId = request.nextUrl.searchParams.get("fileId")?.trim();
  if (!fileId) {
    return NextResponse.json({ detail: "fileId is required" }, { status: 400 });
  }

  const authorization = await authorizeCnoocPlatformToken(request);
  if (!authorization.authorized) {
    return buildCnoocPlatformTokenRejection(authorization.reason);
  }

  const url = new URL(DATA_LAKE_DOWNLOAD_BASE);
  url.searchParams.set("fileId", fileId);

  let response: Response;
  try {
    response = await requestDataLakeDownload(url, authorization.token);
  } catch (error) {
    console.error("Failed to reach data lake download service", {
      error,
      fileId,
      url: url.toString(),
    });
    return NextResponse.json(
      { detail: "Failed to reach data lake download service" },
      { status: 502 },
    );
  }

  if (!response.ok) {
    return NextResponse.json(
      { detail: "Data lake download failed" },
      { status: response.status },
    );
  }

  const headers = new Headers();
  const contentType = response.headers.get("content-type");
  const contentDisposition = response.headers.get("content-disposition");
  if (contentType) headers.set("content-type", contentType);
  if (contentDisposition) {
    headers.set("content-disposition", contentDisposition);
  }

  return new Response(response.body, {
    status: response.status,
    headers,
  });
}
