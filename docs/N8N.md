# n8n Telegram Proxy

Import:

```text
n8n/ai-talapker-n8n-telegram-render-backend-proxy.workflow.json
```

In the Code node set:

```js
const TELEGRAM_BOT_TOKEN = 'PUT_TELEGRAM_BOT_TOKEN_HERE';
const ASSISTANT_API_BASE = 'https://YOUR-BACKEND.onrender.com';
```

Activate workflow.

Telegram webhook must point to the production n8n webhook, not `/webhook-test`:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://n8n.kstu.kz/webhook/ai-talapker-telegram-render"
```

Check current Telegram webhook:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo"
```

Delete old webhook if needed:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/deleteWebhook"
```
