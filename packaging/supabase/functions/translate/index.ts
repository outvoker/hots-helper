// Supabase Edge Function — proxy for VolcEngine MT (火山翻译).
//
// Why a server-side proxy?  VolcEngine signs requests with a SecretKey.
// We can't ship the key inside the .exe (anyone with a hex editor would
// extract it). So the squad runs this Function on Supabase, the .exe
// authenticates with the public anon key (rate-limited per IP), and
// the Function does the SigV4-style signing on its own with secrets
// pulled from `Deno.env`.
//
// Deploy from the repo root:
//
//   supabase secrets set \
//     VOLC_ACCESS_KEY_ID=AKLT… \
//     VOLC_SECRET_ACCESS_KEY=…
//   supabase functions deploy translate --project-ref <your-ref>
//
// Public surface:
//   POST /functions/v1/translate
//   body  { "texts": string[], "target": "zh"|"en"|"ko"|"ja", "source"?: string }
//   200   { "translations": [{ "text": string, "source": string }, …] }
//
// VolcEngine MT API reference:
//   https://www.volcengine.com/docs/4640/65067
//   Region:  cn-north-1
//   Service: translate
//   Action:  TranslateText (Version 2020-06-01)

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { hmac } from "https://deno.land/x/hmac@v2.0.1/mod.ts";

const REGION = "cn-north-1";
const SERVICE = "translate";
const HOST = "translate.volcengineapi.com";
const ENDPOINT = `https://${HOST}`;

const ACCESS_KEY = Deno.env.get("VOLC_ACCESS_KEY_ID") ?? "";
const SECRET_KEY = Deno.env.get("VOLC_SECRET_ACCESS_KEY") ?? "";

const ALLOWED_TARGETS = new Set(["zh", "en", "ko", "ja"]);

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(input: string): Promise<string> {
  const buf = new TextEncoder().encode(input);
  const hash = await crypto.subtle.digest("SHA-256", buf);
  return bytesToHex(new Uint8Array(hash));
}

function hmacSha256(key: Uint8Array | string, data: string): Uint8Array {
  // The volc-style hmac chain expects raw bytes for the *next* HMAC's
  // key, so we use the binary output of `hmac` rather than the hex one.
  const out = hmac("sha256", key, data, undefined, "buffer") as Uint8Array;
  return out;
}

async function signedRequest(payload: string, action: string, version: string): Promise<Response> {
  // VolcEngine uses an AWS-SigV4 derivative. Datestamp is YYYYMMDD,
  // timestamp is YYYYMMDDTHHMMSSZ.
  const now = new Date();
  const dateStamp = now.toISOString().slice(0, 10).replace(/-/g, "");
  const amzDate =
    dateStamp + "T" +
    now.toISOString().slice(11, 19).replace(/:/g, "") + "Z";

  const canonicalQuery = `Action=${action}&Version=${version}`;
  const payloadHash = await sha256Hex(payload);
  const canonicalHeaders =
    `content-type:application/json\n` +
    `host:${HOST}\n` +
    `x-content-sha256:${payloadHash}\n` +
    `x-date:${amzDate}\n`;
  const signedHeaders = "content-type;host;x-content-sha256;x-date";

  const canonicalRequest = [
    "POST",
    "/",
    canonicalQuery,
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");

  const credentialScope = `${dateStamp}/${REGION}/${SERVICE}/request`;
  const stringToSign = [
    "HMAC-SHA256",
    amzDate,
    credentialScope,
    await sha256Hex(canonicalRequest),
  ].join("\n");

  // Derive signing key.
  const kDate = hmacSha256(SECRET_KEY, dateStamp);
  const kRegion = hmacSha256(kDate, REGION);
  const kService = hmacSha256(kRegion, SERVICE);
  const kSigning = hmacSha256(kService, "request");
  const signature = bytesToHex(hmacSha256(kSigning, stringToSign));

  const authHeader =
    `HMAC-SHA256 Credential=${ACCESS_KEY}/${credentialScope}, ` +
    `SignedHeaders=${signedHeaders}, Signature=${signature}`;

  return await fetch(`${ENDPOINT}/?${canonicalQuery}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Host": HOST,
      "X-Content-Sha256": payloadHash,
      "X-Date": amzDate,
      "Authorization": authHeader,
    },
    body: payload,
  });
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: cors });
  }
  if (req.method !== "POST") {
    return new Response("method not allowed", { status: 405, headers: cors });
  }
  if (!ACCESS_KEY || !SECRET_KEY) {
    return new Response(
      JSON.stringify({ error: "translate function: VOLC_* secrets not set" }),
      { status: 500, headers: { ...cors, "Content-Type": "application/json" } },
    );
  }

  let body: { texts?: string[]; target?: string; source?: string };
  try {
    body = await req.json();
  } catch {
    return new Response(
      JSON.stringify({ error: "invalid JSON" }),
      { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
    );
  }
  const texts = Array.isArray(body?.texts) ? body.texts.filter((t): t is string => typeof t === "string") : [];
  const target = (body?.target ?? "zh").toLowerCase();
  if (!ALLOWED_TARGETS.has(target)) {
    return new Response(
      JSON.stringify({ error: `unsupported target language: ${target}` }),
      { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
    );
  }
  if (texts.length === 0) {
    return new Response(
      JSON.stringify({ translations: [] }),
      { headers: { ...cors, "Content-Type": "application/json" } },
    );
  }
  if (texts.length > 50) {
    return new Response(
      JSON.stringify({ error: "too many texts; max 50 per request" }),
      { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
    );
  }

  const payload = JSON.stringify({
    SourceLanguage: body?.source && body.source !== "auto" ? body.source : undefined,
    TargetLanguage: target,
    TextList: texts,
  });

  const resp = await signedRequest(payload, "TranslateText", "2020-06-01");
  if (!resp.ok) {
    const errBody = await resp.text();
    return new Response(
      JSON.stringify({ error: `volc upstream ${resp.status}`, detail: errBody.slice(0, 400) }),
      { status: 502, headers: { ...cors, "Content-Type": "application/json" } },
    );
  }
  const upstream = await resp.json();
  // VolcEngine response: { ResponseMetadata: …, TranslationList: [{ Translation, DetectedSourceLanguage }, …] }
  const list = (upstream?.TranslationList ?? []).map((row: { Translation?: string; DetectedSourceLanguage?: string }) => ({
    text: row?.Translation ?? "",
    source: row?.DetectedSourceLanguage ?? "",
  }));
  return new Response(
    JSON.stringify({ translations: list }),
    { headers: { ...cors, "Content-Type": "application/json" } },
  );
});
