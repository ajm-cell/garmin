# Garmin Connect connector for Claude

A small, read-only MCP server that lets Claude read your own Garmin Connect data
through Garmin's **Personal API** (launched October 2026). Nothing in it can change
or delete anything in your Garmin account.

## What Claude can read

| Tool | What it returns |
| --- | --- |
| `list_recent_activities` | Recent runs, rides, gym sessions, golf rounds with time, distance, HR, training effect and load |
| `get_activity` | Full detail for one activity |
| `get_activity_workout` | Sets, reps and weights for a strength session |
| `get_daily_heart_rate` | Resting, min and max HR for a day |
| `get_sleep` / `get_sleep_summaries` | Sleep score, stages, overnight HRV, for one night or a range |
| `get_daily_summaries` | Steps, calories, intensity minutes, stress, Body Battery |
| `get_hrv` | Overnight HRV trend (recovery) |
| `get_vo2_max` | VO2 max estimates |

## How the Personal API key works

1. Your key is exchanged for a 24-hour access token at
   `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token`
   with the header `Authorization: ApiKey <your key>`.
2. The token is then sent as `Authorization: Bearer <token>` to the data endpoints.
   The server does this for you and renews the token automatically.
3. Keys last 30, 90, 180 or 365 days. Garmin emails you a week before expiry.
   When it expires, make a new key and update the `GARMIN_PERSONAL_API_KEY` setting on your host.

Sources: [Garmin Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/),
[FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/),
[Garmin's Python client](https://github.com/garmin/connect-personal-api-python).

## Step 1. Create your Garmin key

1. Sign in at [Garmin Connect, Personal API Key](https://connect.garmin.com/app/settings/personalApiToken).
2. Choose **Create Key**, accept the terms, pick an expiry (one year is least hassle), then **Create**.
3. Copy the key somewhere safe, like a password manager. Garmin only shows it once.
   Don't paste it into chats or commit it to GitHub.

## Step 2. Make a connector secret

The server only answers at a secret address, so nobody who finds your host can read
your data. Make a long random string, for example on a Mac or Linux terminal:

```
openssl rand -hex 24
```

Or use a password manager to generate 40+ random letters and numbers. Keep it with your key.

## Step 3. Host it with an HTTPS address (Render, about 10 minutes)

Any host that runs a Docker container and gives you HTTPS will do. Render is the
simplest:

1. Create a **private** GitHub repository and upload the files in this folder
   (`server.py`, `requirements.txt`, `Dockerfile`, `.gitignore`, `README.md`).
2. At [render.com](https://render.com), choose **New**, then **Web Service**, and pick that repository.
   Render detects the Dockerfile.
3. Under **Environment Variables** add:
   - `GARMIN_PERSONAL_API_KEY` = your Garmin key
   - `CONNECTOR_SECRET` = the random string from step 2
4. Create the service. When it shows **Live**, note its address, for example
   `https://garmin-connector-ab12.onrender.com`. Opening `/health` on it should show `ok`.

Note: Render's free plan sleeps after 15 minutes idle, so the first request after a
break can take up to a minute. The cheapest paid plan avoids that.

**Alternative, run it at home:** run `docker build -t garmin-mcp . && docker run -e GARMIN_PERSONAL_API_KEY=... -e CONNECTOR_SECRET=... -p 8000:8000 garmin-mcp`
and expose it with [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
(`cloudflared tunnel --url http://localhost:8000`) to get an HTTPS address.
It only works while that machine is on.

## Step 4. Add it to Claude

1. In claude.ai, open **Settings**, then **Connectors**, then **Add custom connector**.
2. Name: `Garmin Connect`.
3. URL: your host address + `/` + your secret + `/mcp`, for example
   `https://garmin-connector-ab12.onrender.com/3f9c...e1/mcp`
4. Leave the OAuth fields empty and choose **Add**.
5. In a chat, turn on the Garmin Connect connector from the tools menu and ask something like
   "List my activities from the last two weeks".

Treat that full URL like a password, since anyone with it can read your Garmin data.
If it ever leaks, change `CONNECTOR_SECRET` on Render and update the URL in Claude.

## Test locally (optional)

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GARMIN_PERSONAL_API_KEY=...   # your key
export CONNECTOR_SECRET=$(openssl rand -hex 24)
python server.py
# MCP endpoint: http://localhost:8000/$CONNECTOR_SECRET/mcp
```
