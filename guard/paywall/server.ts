// Hyperion Guard paywall: x402 payment proxy in front of the Guard API.
//
// Agents pay per risk check in USDC through Circle Gateway Nanopayments:
// the middleware answers an unpaid POST /v1/check with 402 and the payment
// requirements, verifies the buyer's signed authorization, and settles it
// with Gateway in batches. Paid requests are forwarded to the Guard API,
// which listens on localhost only. Health, agent lookups and verdict proofs
// are free, so anyone can audit a verdict without paying.
//
//   SELLER_ADDRESS           EVM address that receives the USDC
//   GATEWAY_FACILITATOR_URL  default https://gateway-api-testnet.circle.com
//   GATEWAY_NETWORKS         optional, comma-separated CAIP-2 ids (e.g. eip155:5042002);
//                            omit to accept every network Gateway supports
//   GUARD_PRICE              default $0.001 per check
//   GUARD_UPSTREAM           default http://127.0.0.1:8100
//   PORT                     default 8080
//
// Run: node server.ts (Node 22.6+ runs TypeScript directly).

import express from "express";
import type { Request, Response } from "express";
import { createGatewayMiddleware } from "@circle-fin/x402-batching/server";

const env = (name: string, fallback?: string): string => {
  const value = process.env[name] ?? fallback;
  if (!value) {
    console.error(`set ${name}`);
    process.exit(1);
  }
  return value;
};

const sellerAddress = env("SELLER_ADDRESS");
if (!/^0x[0-9a-fA-F]{40}$/.test(sellerAddress)) {
  console.error("SELLER_ADDRESS must be a 0x-prefixed 20-byte address");
  process.exit(1);
}
const upstream = env("GUARD_UPSTREAM", "http://127.0.0.1:8100");
const price = env("GUARD_PRICE", "$0.001");
const networks = process.env.GATEWAY_NETWORKS?.split(",").map((n) => n.trim()).filter(Boolean);

const gateway = createGatewayMiddleware({
  sellerAddress,
  facilitatorUrl: env("GATEWAY_FACILITATOR_URL", "https://gateway-api-testnet.circle.com"),
  ...(networks?.length ? { networks } : {}),
});

type PaidRequest = Request & { payment?: { payer: string; amount: string; network: string } };

async function forward(req: PaidRequest, res: Response) {
  try {
    const hasBody = req.method !== "GET" && req.method !== "HEAD";
    const r = await fetch(upstream + req.originalUrl, {
      method: req.method,
      headers: hasBody ? { "content-type": "application/json" } : {},
      body: hasBody ? JSON.stringify(req.body ?? {}) : undefined,
    });
    if (req.payment) res.setHeader("x-guard-paid-by", req.payment.payer);
    res.status(r.status).type(r.headers.get("content-type") ?? "application/json").send(await r.text());
  } catch (err) {
    res.status(502).json({ detail: `Guard API unreachable: ${(err as Error).message}` });
  }
}

const app = express();
app.use(express.json({ limit: "64kb" }));
app.post("/v1/check", gateway.require(price), forward);
app.get(["/v1/health", "/v1/agents/:address", "/v1/verdicts", "/v1/verdicts/:seq"], forward);

const port = Number(env("PORT", "8080"));
app.listen(port, () => console.log(`Hyperion Guard paywall on :${port} → ${upstream} (${price} per check)`));
