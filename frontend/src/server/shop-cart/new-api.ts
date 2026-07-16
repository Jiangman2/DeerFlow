import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { Readable } from "node:stream";

import type { NewShopCartFile } from "@/core/shop-cart/api-files";
import { env } from "@/env";

const DEFAULT_NEW_SHOP_CART_API_BASE_URL = "https://datalake.cnooc";

export function getNewShopCartApiBaseUrl() {
  return (
    env.SHOP_CART_API_BASE_URL?.trim() ?? DEFAULT_NEW_SHOP_CART_API_BASE_URL
  ).replace(/\/+$/, "");
}

export function buildAuthorizationHeaders(token: string | null) {
  const headers = new Headers();
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }
  return headers;
}

export function requestDataLakeJson(
  url: URL,
  token: string | null,
): Promise<Response> {
  return new Promise((resolve, reject) => {
    const request = url.protocol === "https:" ? httpsRequest : httpRequest;
    const upstreamRequest = request(
      url,
      {
        method: "GET",
        headers: Object.fromEntries(buildAuthorizationHeaders(token)),
        rejectUnauthorized: env.SHOP_CART_DOWNLOAD_TLS_VERIFY !== "false",
      },
      (upstreamResponse) => {
        const responseHeaders = new Headers();
        for (const [name, value] of Object.entries(upstreamResponse.headers)) {
          if (Array.isArray(value)) {
            responseHeaders.set(name, value.join(", "));
          } else if (value) {
            responseHeaders.set(name, value);
          }
        }

        const responseBody = Readable.toWeb(
          upstreamResponse,
        ) as unknown as BodyInit;
        resolve(
          new Response(responseBody, {
            status: upstreamResponse.statusCode ?? 502,
            headers: responseHeaders,
          }),
        );
      },
    );

    upstreamRequest.on("error", reject);
    upstreamRequest.end();
  });
}

export function requestDataLakeBinary(
  url: URL,
  {
    body,
    token,
  }: {
    body: NewShopCartFile[];
    token: string | null;
  },
): Promise<Response> {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const headers = new Headers(buildAuthorizationHeaders(token));
    headers.set("content-type", "application/json");
    headers.set("content-length", String(Buffer.byteLength(payload)));

    const request = url.protocol === "https:" ? httpsRequest : httpRequest;
    const upstreamRequest = request(
      url,
      {
        method: "POST",
        headers: Object.fromEntries(headers),
        rejectUnauthorized: env.SHOP_CART_DOWNLOAD_TLS_VERIFY !== "false",
      },
      (upstreamResponse) => {
        const responseHeaders = new Headers();
        for (const [name, value] of Object.entries(upstreamResponse.headers)) {
          if (Array.isArray(value)) {
            responseHeaders.set(name, value.join(", "));
          } else if (value) {
            responseHeaders.set(name, value);
          }
        }

        const responseBody = Readable.toWeb(
          upstreamResponse,
        ) as unknown as BodyInit;
        resolve(
          new Response(responseBody, {
            status: upstreamResponse.statusCode ?? 502,
            headers: responseHeaders,
          }),
        );
      },
    );

    upstreamRequest.on("error", reject);
    upstreamRequest.write(payload);
    upstreamRequest.end();
  });
}
