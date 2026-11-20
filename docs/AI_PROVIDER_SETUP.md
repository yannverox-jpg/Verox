# Verox free AI provider setup

Verox prefers the direct Google Gemini API free tier. It does not automatically fall back to a paid provider.

## Configure the free provider

1. Open [Google AI Studio](https://aistudio.google.com/apikey) and create an API key for the Gemini Developer API free tier.
2. In the Vercel project **verox-brain**, open Settings → Environment Variables.
3. Add `GEMINI_API_KEY` as an **Encrypted** variable for Production, Preview, and Development.
4. Keep `GEMINI_MODEL` set to `gemini-3.8-flash`. Verox defaults to that model if the variable is absent.
5. Redeploy Production, then verify `/api/health` reports `gemini_configured: true` and submit a harmless test prompt.

The free tier has rate limits and may use submitted prompts and responses to improve Google's products. Do not send passwords, API secrets, private financial data, or proprietary material unless you accept those data terms. Model availability and free-tier limits are controlled by Google and may change.

## Billing safety

- Do not add a credit card merely to unlock Vercel AI Gateway. The current Verox account received a `customer_verification_required` response from that gateway.
- Verox uses AI Gateway only when `AI_GATEWAY_API_KEY` is explicitly configured.
- If neither `GEMINI_API_KEY` nor an explicit `AI_GATEWAY_API_KEY` is configured, the task fails clearly. It will not call OpenAI, Anthropic, xAI, or another paid provider as a hidden fallback.
