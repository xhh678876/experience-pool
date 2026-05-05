#!/usr/bin/env node
import http from "node:http";
import net from "node:net";
import process from "node:process";

const gatewayHost = process.env.EXP_GATEWAY_HOST || "0.0.0.0";
const gatewayPort = Number(process.env.EXP_GATEWAY_PORT || "3080");
const apiOrigin = new URL(
  process.env.EXP_API_ORIGIN || `http://127.0.0.1:${process.env.EXP_API_PORT || "8080"}`,
);
const uiOrigin = new URL(
  process.env.EXP_UI_ORIGIN || `http://127.0.0.1:${process.env.EXP_UI_PORT || "3000"}`,
);
const startedAt = new Date().toISOString();

const hopByHopHeaders = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

// FastAPI handles its own /v1/* + docs + bootstrap files (install.sh,
// uploader, etc.). Anything not matched here is treated as UI.
const apiExactPaths = new Set([
  "/healthz",
  "/openapi.json",
  "/install",
  "/install.sh",
  "/exp_uploader.py",
  "/exp_annotator.py",
  "/exp_consent.py",
  "/session_start.sh",
  "/agent-contract.md",
  "/opf_service.py",
  "/opf_filter.py",
  "/session-extractor/run.sh",
  "/session-extractor/extract_and_upload.py",
  "/session-extractor/README.md",
]);

function routeForPath(pathname) {
  if (
    apiExactPaths.has(pathname) ||
    pathname.startsWith("/v1/") ||
    pathname.startsWith("/docs") ||
    pathname.startsWith("/redoc")
  ) {
    return { label: "api", origin: apiOrigin };
  }
  return { label: "ui", origin: uiOrigin };
}

function securityHeaders() {
  return {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
  };
}

function forwardedHeaders(req, origin) {
  const headers = {};
  for (const [name, value] of Object.entries(req.headers)) {
    if (!hopByHopHeaders.has(name.toLowerCase())) {
      headers[name] = value;
    }
  }
  headers.host = origin.host;
  headers["x-forwarded-host"] = req.headers.host || "";
  headers["x-forwarded-proto"] = "http";
  headers["x-forwarded-for"] = [req.socket.remoteAddress, req.headers["x-forwarded-for"]]
    .filter(Boolean)
    .join(", ");
  return headers;
}

function responseHeaders(upstreamHeaders, routeLabel) {
  const headers = {};
  for (const [name, value] of Object.entries(upstreamHeaders)) {
    if (!hopByHopHeaders.has(name.toLowerCase())) {
      headers[name] = value;
    }
  }
  return {
    ...headers,
    ...securityHeaders(),
    "x-gateway-route": routeLabel,
  };
}

function writeJson(res, statusCode, payload) {
  res.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    ...securityHeaders(),
  });
  res.end(`${JSON.stringify(payload, null, 2)}\n`);
}

function probe(origin, path) {
  return new Promise((resolve) => {
    const req = http.request(
      {
        protocol: origin.protocol,
        hostname: origin.hostname,
        port: origin.port || "80",
        method: "GET",
        path,
        timeout: 1500,
        headers: {
          host: origin.host,
        },
      },
      (res) => {
        res.resume();
        resolve({
          status: res.statusCode && res.statusCode < 500 ? "ok" : "bad",
          status_code: res.statusCode || 0,
        });
      },
    );

    req.on("timeout", () => {
      req.destroy(new Error("timeout"));
    });
    req.on("error", (error) => {
      resolve({
        status: "error",
        message: error.message,
      });
    });
    req.end();
  });
}

function proxyRequest(req, res, route) {
  const upstreamReq = http.request(
    {
      protocol: route.origin.protocol,
      hostname: route.origin.hostname,
      port: route.origin.port || "80",
      method: req.method,
      path: req.url || "/",
      headers: forwardedHeaders(req, route.origin),
    },
    (upstreamRes) => {
      res.writeHead(
        upstreamRes.statusCode || 502,
        responseHeaders(upstreamRes.headers, route.label),
      );
      upstreamRes.pipe(res);
    },
  );

  upstreamReq.on("error", (error) => {
    writeJson(res, 502, {
      status: "bad_gateway",
      route: route.label,
      upstream: route.origin.origin,
      message: error.message,
    });
  });

  req.pipe(upstreamReq);
}

function writeUpgradeRequest(req, upstreamSocket, route) {
  const headers = forwardedHeaders(req, route.origin);
  headers.connection = "Upgrade";
  headers.upgrade = req.headers.upgrade || "websocket";

  const lines = [`${req.method} ${req.url || "/"} HTTP/${req.httpVersion}`];
  for (const [name, value] of Object.entries(headers)) {
    if (Array.isArray(value)) {
      for (const item of value) {
        lines.push(`${name}: ${item}`);
      }
    } else if (value !== undefined) {
      lines.push(`${name}: ${value}`);
    }
  }
  upstreamSocket.write(`${lines.join("\r\n")}\r\n\r\n`);
}

const server = http.createServer(async (req, res) => {
  const requestUrl = new URL(req.url || "/", "http://local.gateway");

  if (requestUrl.pathname === "/__gateway/health") {
    const [apiCheck, uiCheck] = await Promise.all([
      probe(apiOrigin, "/healthz"),
      probe(uiOrigin, "/"),
    ]);
    const ok = apiCheck.status === "ok" && uiCheck.status === "ok";
    writeJson(res, ok ? 200 : 503, {
      status: ok ? "ok" : "degraded",
      gateway: "node-local",
      started_at: startedAt,
      routes: {
        api: apiOrigin.origin,
        ui: uiOrigin.origin,
      },
      checks: {
        api: apiCheck,
        ui: uiCheck,
      },
    });
    return;
  }

  proxyRequest(req, res, routeForPath(requestUrl.pathname));
});

server.on("upgrade", (req, socket, head) => {
  const requestUrl = new URL(req.url || "/", "http://local.gateway");
  const route = routeForPath(requestUrl.pathname);
  const upstreamSocket = net.connect(Number(route.origin.port || "80"), route.origin.hostname);

  upstreamSocket.on("connect", () => {
    writeUpgradeRequest(req, upstreamSocket, route);
    if (head.length > 0) {
      upstreamSocket.write(head);
    }
    upstreamSocket.pipe(socket);
    socket.pipe(upstreamSocket);
  });

  upstreamSocket.on("error", () => {
    socket.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n");
  });
});

server.listen(gatewayPort, gatewayHost, () => {
  process.stdout.write(
    [
      `Experience Pool local gateway listening on http://${gatewayHost}:${gatewayPort}`,
      `UI upstream:  ${uiOrigin.origin}`,
      `API upstream: ${apiOrigin.origin}`,
    ].join("\n") + "\n",
  );
});
