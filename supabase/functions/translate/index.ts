// Supabase Edge Function — proxy for VolcEngine MT (火山翻译).
//
// Why a server-side proxy?  VolcEngine signs requests with a SecretKey.
// We can't ship the key inside the .exe (anyone with a hex editor —
// or a .pyc decompiler — would extract it). The squad runs this
// Function on Supabase, the .exe authenticates with the public anon
// key, and the Function does the SigV4-style signing on its own with
// secrets pulled from Deno.env.
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

const REGION = "cn-north-1";
const SERVICE = "translate";
const HOST = "translate.volcengineapi.com";
const ENDPOINT = `https://${HOST}`;

const ACCESS_KEY = Deno.env.get("VOLC_ACCESS_KEY_ID") ?? "";
const SECRET_KEY = Deno.env.get("VOLC_SECRET_ACCESS_KEY") ?? "";

const ALLOWED_TARGETS = new Set(["zh", "en", "ko", "ja"]);

interface TranslateRequestBody {
  texts?: string[];
  target?: string;
  source?: string;
}

interface VolcTranslationItem {
  Translation?: string;
  DetectedSourceLanguage?: string;
}

interface ResponseTranslation {
  text: string;
  source: string;
}

const cors: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Hex(input: string): Promise<string> {
  const buf = new TextEncoder().encode(input);
  const hash = await crypto.subtle.digest("SHA-256", buf);
  return bytesToHex(new Uint8Array(hash));
}

async function importHmacKey(key: ArrayBuffer | Uint8Array): Promise<CryptoKey> {
  // crypto.subtle.importKey wants a BufferSource; ArrayBuffer / Uint8Array both qualify.
  return crypto.subtle.importKey(
    "raw",
    key,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

async function hmacSha256(
  key: ArrayBuffer | Uint8Array,
  data: string,
): Promise<Uint8Array> {
  const cryptoKey = await importHmacKey(key);
  const sig = await crypto.subtle.sign(
    "HMAC",
    cryptoKey,
    new TextEncoder().encode(data),
  );
  return new Uint8Array(sig);
}

async function signedRequest(
  payload: string,
  action: string,
  version: string,
): Promise<Response> {
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
  const kDate = await hmacSha256(
    new TextEncoder().encode(SECRET_KEY),
    dateStamp,
  );
  const kRegion = await hmacSha256(kDate, REGION);
  const kService = await hmacSha256(kRegion, SERVICE);
  const kSigning = await hmacSha256(kService, "request");
  const sig = await hmacSha256(kSigning, stringToSign);
  const signature = bytesToHex(sig);

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

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: cors });
  }
  if (req.method !== "POST") {
    return new Response("method not allowed", { status: 405, headers: cors });
  }
  if (!ACCESS_KEY || !SECRET_KEY) {
    return jsonResponse(
      { error: "translate function: VOLC_* secrets not set" },
      500,
    );
  }

  let body: TranslateRequestBody;
  try {
    body = await req.json();
  } catch {
    return jsonResponse({ error: "invalid JSON" }, 400);
  }
  const texts = Array.isArray(body?.texts)
    ? body.texts.filter((t: unknown): t is string => typeof t === "string")
    : [];
  const target = (body?.target ?? "zh").toLowerCase();
  if (!ALLOWED_TARGETS.has(target)) {
    return jsonResponse(
      { error: `unsupported target language: ${target}` },
      400,
    );
  }
  if (texts.length === 0) {
    return jsonResponse({ translations: [] });
  }
  if (texts.length > 50) {
    return jsonResponse(
      { error: "too many texts; max 50 per request" },
      400,
    );
  }

  const upstreamPayload: Record<string, unknown> = {
    TargetLanguage: target,
    TextList: texts,
  };
  if (body?.source && body.source !== "auto") {
    upstreamPayload.SourceLanguage = body.source;
  }
  const payload = JSON.stringify(upstreamPayload);

  let resp: Response;
  try {
    resp = await signedRequest(payload, "TranslateText", "2020-06-01");
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    return jsonResponse({ error: `signing/fetch failed: ${msg}` }, 502);
  }
  if (!resp.ok) {
    const errBody = await resp.text();
    return jsonResponse(
      { error: `volc upstream ${resp.status}`, detail: errBody.slice(0, 400) },
      502,
    );
  }
  const upstream = await resp.json() as { TranslationList?: VolcTranslationItem[] };
  const list: ResponseTranslation[] = (upstream?.TranslationList ?? []).map(
    (row) => ({
      text: row?.Translation ?? "",
      source: row?.DetectedSourceLanguage ?? "",
    }),
  );
  return jsonResponse({ translations: list });
});
