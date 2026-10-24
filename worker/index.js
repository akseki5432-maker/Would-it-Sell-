// Would it Sell? on Cloudflare Workers.
//
// Static files in worker/public (the chat page) are served by Cloudflare directly.
// This Worker only handles POST /api/chat: it calls the Anthropic API with the
// ANTHROPIC_API_KEY secret and streams the reply back as server-sent events:
//   data: {"t": "text chunk"}   reply text
//   data: {"error": "..."}      the reply failed
//   data: {"done": true}        the reply is complete

import Anthropic from "@anthropic-ai/sdk";

const MODEL = "claude-sonnet-5-5";
const MAX_CHARS = 4000;
const MAX_HISTORY = 30;
const MAX_CONTINUATIONS = 5;

const SYSTEM_PROMPT = `You are "Would it Sell?", a friendly, candid market analyst inside a chat app. People bring ideas (businesses, physical products, software tools, services) and you help them judge whether the idea can make money, over an ongoing conversation.

How to work:
- Talk like a sharp advisor. Get straight to the answer, with no preamble. Keep replies focused: short paragraphs, bullets, bold key numbers. Reply in the user's language.
- Never talk about yourself, your model, your tools, your training data, or what you can or can't access, and never add AI disclaimers. If asked who you are, you are the Would it Sell? analyst.
- If a crucial detail is missing (where they sell, target customer, budget, price), ask one or two quick questions. If the idea is clear enough, give your read right away and ask while you share it.
- Back claims with real evidence. Use web search for competitors, their prices, reviews, regulation and recent news. Cite sources inline as markdown links. Give numbers as realistic ranges.
- When you discuss price, costs or profit, show the math: gross margin per unit, break-even units per month, lifetime value vs. customer acquisition cost.
- After you have evaluated an idea properly, include ONE scorecard block in your reply, exactly in this format (the app hides it and shows a visual card, so don't repeat its contents in prose):
~~~scorecard
{"idea_summary": "...", "category": "business|physical_product|software_tool|service|other",
 "confidence": "low|medium|high",
 "dimensions": {"market_demand": {"score": 0-10, "why": "..."}, "monetization": {...}, "competition": {...},
   "feasibility": {...}, "timing": {...}},
 "target_customers": ["..."], "competitors": [{"name": "...", "note": "what they sell and their price"}],
 "pricing_benchmark": "...", "revenue_model": "...", "key_risks": ["..."], "validation_steps": ["cheap experiment"]}
~~~
  Competition: 10 means an open market, 0 means saturated with strong, cheap players. The app computes the overall score (weights: demand 30%, monetization 25%, competition, feasibility and timing 15% each). After the block, explain the verdict briefly. Score again with a new block whenever the idea changes meaningfully.
- Be honest. Say clearly when the evidence points against an idea and suggest pivots that could work. End every evaluation with cheap ways to test it with real customers.`.replace(/~~~/g, "```");

const GENERIC_ERROR = "Something interrupted the reply. Send your message again.";

function jsonError(status, error) {
  return new Response(JSON.stringify({ error }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// Keep valid user/assistant text turns, newest MAX_HISTORY, starting on a user turn.
function cleanHistory(raw) {
  if (!Array.isArray(raw)) return null;
  let history = raw
    .filter((m) => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string" && m.content.trim())
    .map((m) => ({ role: m.role, content: m.content }))
    .slice(-MAX_HISTORY);
  while (history.length && history[0].role !== "user") history = history.slice(1);
  return history;
}

// After a mid-output model fallback, the API asks clients not to echo back
// thinking/tool_use blocks (or unpaired server tool calls) from before the switch.
export function echoableContent(content) {
  let boundary = -1;
  content.forEach((b, i) => { if (b.type === "fallback") boundary = i; });
  if (boundary < 0) return content;
  const resultIds = new Set(content.filter((b) => b.type.endsWith("_tool_result")).map((b) => b.tool_use_id));
  return content.filter((b, i) => {
    if (i >= boundary) return true;
    if (b.type === "text" || b.type.endsWith("_tool_result")) return true;
    return b.type === "server_tool_use" && resultIds.has(b.id);
  });
}

async function handleChat(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, GENERIC_ERROR);
  }
  const history = cleanHistory(body?.messages);
  const last = history?.at(-1);
  if (!last || last.role !== "user") return jsonError(400, GENERIC_ERROR);
  if (last.content.length > MAX_CHARS) return jsonError(400, "That message is too long. Keep it under 4,000 characters.");

  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  if (env.CHAT_LIMITER) {
    const { success } = await env.CHAT_LIMITER.limit({ key: ip });
    if (!success) return jsonError(429, "You're sending messages too quickly. Wait a minute and try again.");
  }

  if (!env.ANTHROPIC_API_KEY) {
    console.error("chat: missing ANTHROPIC_API_KEY secret");
    return jsonError(500, GENERIC_ERROR);
  }
  const client = new Anthropic({
    apiKey: env.ANTHROPIC_API_KEY,
    ...(env.ANTHROPIC_BASE_URL ? { baseURL: env.ANTHROPIC_BASE_URL } : {}),
  });

  const messages = [...history];
  const enc = new TextEncoder();
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();
  const send = (obj) => writer.write(enc.encode(`data: ${JSON.stringify(obj)}\n\n`)).catch(() => {});

  const run = (async () => {
    try {
      for (let i = 0; i <= MAX_CONTINUATIONS; i++) {
        const stream = client.beta.messages.stream(
          {
            model: MODEL,
            max_tokens: 16000,
            output_config: { effort: "medium" },
            system: SYSTEM_PROMPT,
            messages,
            tools: [{ type: "web_search_20260209", name: "web_search", max_uses: 5 }],
            betas: ["server-side-fallback-2026-07-01"],
            fallbacks: "default",
          },
          { signal: request.signal },
        );
        stream.on("text", (text) => send({ t: text }));
        const final = await stream.finalMessage();

        if (final.stop_reason === "refusal") {
          send({ error: "That request couldn't be answered. Try rephrasing it." });
          break;
        }
        if (final.stop_reason === "pause_turn" && i < MAX_CONTINUATIONS) {
          // The web research paused; send the partial turn back and it resumes.
          messages.push({ role: "assistant", content: echoableContent(final.content) });
          continue;
        }
        break;
      }
      send({ done: true });
    } catch (e) {
      if (!request.signal?.aborted) {
        console.error("chat error", e?.status ?? "", e?.message ?? e);
        send({ error: GENERIC_ERROR });
      }
    } finally {
      writer.close().catch(() => {});
    }
  })();

  return { response: new Response(readable, {
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
  }), run };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/api/chat") {
      if (request.method !== "POST") return jsonError(405, "Use POST.");
      const result = await handleChat(request, env);
      if (result instanceof Response) return result;
      ctx.waitUntil(result.run);
      return result.response;
    }
    // Anything that isn't a static file or the API.
    return env.ASSETS ? env.ASSETS.fetch(request) : new Response("Not found", { status: 404 });
  },
};
