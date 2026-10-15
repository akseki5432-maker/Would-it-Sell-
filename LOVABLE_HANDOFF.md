# Would it Sell? — Handoff for Lovable

Everything needed to rebuild **Would it Sell?** in Lovable and put it on your own domain.

The original project (in the zip) is a Python app. Lovable builds React + Supabase apps, so it can't run the
Python code directly. Instead, give Lovable the prompt in **Part 1** and the reference material in **Part 2**.
The full source stays in the zip unchanged, for reference.

---

## Part 1: Paste this into Lovable

> Build a web app called **Would it Sell?**: a chat with an AI market analyst that tells people whether
> their business, product, app or service idea can make money.
>
> **Stack:** React frontend. Supabase Edge Function named `chat` as the backend. The Anthropic API key is stored
> as a Supabase secret named `ANTHROPIC_API_KEY` and is only used inside the Edge Function, never in the browser.
>
> **Chat behavior**
> - One-screen chat layout: header with logo "$" + "Would it Sell?" and a "New chat" button; scrolling message
>   list; message box pinned at the bottom (Enter sends, Shift+Enter new line).
> - Empty state: headline "Got an idea? Let's find out if it sells.", a short explanation, and 4 example idea
>   buttons that send their text when clicked (examples are in Part 2).
> - Users can send many messages; the full conversation history is sent to the backend each time so the
>   analyst remembers context. Keep the conversation in the browser (localStorage) so a refresh keeps it.
> - Stream the reply word by word. While waiting, show "Thinking it through..." with a pulsing dot.
> - Show a Stop button while a reply is streaming.
> - Render replies as Markdown (bold, lists, links, tables).
> - Light and dark mode, works on phones.
>
> **Backend (`chat` Edge Function)**
> - Use the official Anthropic TypeScript SDK (`npm:@anthropic-ai/sdk`).
> - Call `client.messages.stream()` with model `claude-sonnet-5-5`, `max_tokens: 16000`,
>   `output_config: { effort: "medium" }`, the system prompt from Part 2, the conversation messages, and
>   the built-in web search tool: `tools: [{ type: "web_search_20260209", name: "web_search", max_uses: 5 }]`.
> - Stream text deltas to the browser (server-sent events).
> - If the final message has `stop_reason: "pause_turn"`, append the assistant content to the messages and call
>   again (up to 5 times) so the research finishes. Do not add an extra user message.
> - If `stop_reason` is `"refusal"`, return a friendly error: "That request couldn't be answered. Try rephrasing it."
> - Protect the API bill: limit each visitor to 20 messages per hour (by IP, stored in a Supabase table), reject
>   messages longer than 4,000 characters, and send only the last 30 messages of history.
>
> **Scorecard card**
> - When the analyst's reply contains a fenced block starting with ```` ```scorecard ````, hide that block from the
>   text and render it as a visual card instead (format and scoring rules in Part 2). While the block is still
>   streaming, show "Building the scorecard..." in its place.
> - The card shows: a ring with the overall score out of 100, a verdict badge, the idea summary, category and
>   confidence, five bars (one per dimension, with its weight and the "why" text), and collapsible sections:
>   "Test it cheaply first" (open by default), "Customers and money", "Competitors", "Key risks".
> - The overall score and verdict are calculated in the frontend from the five dimension scores, using the
>   exact formula in Part 2. Never trust a score the model writes.
>
> Do not show any text about AI models, "Claude", or what the AI can or can't do anywhere in the interface.

---

## Part 2: Reference material

### System prompt (use exactly)

```text
You are "Would it Sell?", a friendly, candid market analyst inside a chat app. People bring ideas (businesses,
physical products, software tools, services) and you help them judge whether the idea can make money, over an
ongoing conversation.

How to work:
- Talk like a sharp advisor. Get straight to the answer, with no preamble. Keep replies focused: short paragraphs,
  bullets, bold key numbers. Reply in the user's language.
- Never talk about yourself, your model, your tools, your training data, or what you can or can't access, and
  never add AI disclaimers. If asked who you are, you are the Would it Sell? analyst.
- If a crucial detail is missing (where they sell, target customer, budget, price), ask one or two quick
  questions. If the idea is clear enough, give your read right away and ask while you share it.
- Back claims with real evidence. Use web search for competitors, their prices, reviews, regulation and recent
  news. Cite sources inline as markdown links. Give numbers as realistic ranges.
- When you discuss price, costs or profit, show the math: gross margin per unit, break-even units per month,
  lifetime value vs. customer acquisition cost.
- After you have evaluated an idea properly, include ONE scorecard block in your reply, exactly in this format
  (the app hides it and shows a visual card, so don't repeat its contents in prose):
```scorecard
{"idea_summary": "...", "category": "business|physical_product|software_tool|service|other",
 "confidence": "low|medium|high",
 "dimensions": {"market_demand": {"score": 0-10, "why": "..."}, "monetization": {...}, "competition": {...},
   "feasibility": {...}, "timing": {...}},
 "target_customers": ["..."], "competitors": [{"name": "...", "note": "what they sell and their price"}],
 "pricing_benchmark": "...", "revenue_model": "...", "key_risks": ["..."], "validation_steps": ["cheap experiment"]}
```
  Competition: 10 means an open market, 0 means saturated with strong, cheap players. The app computes the
  overall score (weights: demand 30%, monetization 25%, competition, feasibility and timing 15% each). After the
  block, explain the verdict briefly. Score again with a new block whenever the idea changes meaningfully.
- Be honest. Say clearly when the evidence points against an idea and suggest pivots that could work. End every
  evaluation with cheap ways to test it with real customers.
```

### Scoring formula (frontend)

Clamp each dimension score to a whole number from 0 to 10, then:

| Dimension | Key | Weight |
|---|---|---|
| Market demand | `market_demand` | 30% |
| Monetization | `monetization` | 25% |
| Competition (10 = open market) | `competition` | 15% |
| Feasibility | `feasibility` | 15% |
| Timing | `timing` | 15% |

`overall = round(10 × Σ(weight × score))` → a number from 0 to 100.

| Overall score | Verdict | Badge color |
|---|---|---|
| 75 or more | Strong go | green |
| 60–74 | Promising | blue |
| 40–59 | Risky | amber |
| below 40 | Unlikely | red |

Bar colors per dimension: 7+ green, 5–6 blue, 3–4 amber, 0–2 red.

```js
const WEIGHTS = { market_demand: 0.30, monetization: 0.25, competition: 0.15, feasibility: 0.15, timing: 0.15 };
const clamp = (n) => (Number.isFinite(+n) ? Math.max(0, Math.min(10, Math.round(+n))) : 0);
const overall = (dims) =>
  Math.round(10 * Object.entries(WEIGHTS).reduce((s, [k, w]) => s + w * clamp(dims?.[k]?.score), 0));
const verdict = (s) => (s >= 75 ? "Strong go" : s >= 60 ? "Promising" : s >= 40 ? "Risky" : "Unlikely");
```

### Example idea buttons

- A mobile coffee cart for office parks in Austin, about $15k to start
- An app that warns renters when a clause in their lease is about to matter
- Handmade ceramic planters sold on Etsy and at weekend markets
- A smart pet feeder that recognizes which pet is eating

### Look and feel

- Colors (light): background `#f3f5f2`, panels `#ffffff`, text `#18211d`, muted text `#5f6b65`,
  borders `#dde3df`, accent green `#17694f`.
- Colors (dark): background `#121815`, panels `#1a221e`, text `#e6ece8`, muted `#93a19a`, borders `#2a3530`,
  accent `#4cbf93`.
- Verdict colors: green `#1d8a4f`, blue `#2f6fc6`, amber `#b97512`, red `#be3d2a` (lighter in dark mode).
- Fonts (Google Fonts): **Bricolage Grotesque** for headings and the score number, **IBM Plex Sans** for text,
  **IBM Plex Mono** for small labels.
- User messages: green bubbles on the right. Analyst replies: plain text on the left, full width.

### Optional extra data sources (phase 2)

The original Python app also pulled live data from these free APIs (no keys needed). Lovable can add them later
as extra tools the Edge Function offers the model. Implementations are in `would_it_sell/sources.py` in the zip.

| Source | Endpoint | What it gives |
|---|---|---|
| Hacker News | `https://hn.algolia.com/api/v1/search?query=...&tags=story` | Tech interest, year over year |
| Reddit | `https://www.reddit.com/search.json?q=...&t=year` | Communities discussing the problem |
| Wikipedia pageviews | `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/...` | 24-month interest trend |
| GitHub | `https://api.github.com/search/repositories?q=...` | Existing open-source competitors |
| World Bank | `https://api.worldbank.org/v2/country/{code}/indicator/{id}?format=json` | Population, income, inflation |

---

## Connecting your domain

1. Buy the domain on Cloudflare: https://dash.cloudflare.com/?to=/:account/domains/register
2. In Lovable, open **Project settings → Domains**, add your domain, and follow the DNS records it shows.
3. Add those records in Cloudflare under your domain's **DNS** page. If Lovable asks for an A record or CNAME,
   set the Cloudflare proxy (orange cloud) to **DNS only** (grey) until Lovable confirms the domain is verified.

## What's in the zip

| Path | What it is |
|---|---|
| `would_it_sell/chat.py` | The chat engine and the full system prompt (with live data tools) |
| `would_it_sell/sources.py` | Live data sources and the unit-economics calculator |
| `would_it_sell/models.py` | Scorecard fields and scoring weights |
| `would_it_sell/web/static/index.html` | The browser chat page for the Python app |
| `artifact/would-it-sell.html` | The single-file page published on claude.ai |
| `tests/` | 35 automated tests |
| `README.md` | How to run the Python app locally |
