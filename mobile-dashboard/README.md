# Tele-GoldBCH Mobile Dashboard

Live trading dashboard for the Tele-GoldBCH forex bot. Reads state from Upstash Redis (pushed by the Python bot every 15 min) and auto-refreshes every 10s.

## Stack
- Next.js 15 (App Router)
- TypeScript + Tailwind CSS
- Upstash Redis (free tier)

## Local dev
```bash
cp .env.example .env.local
# Fill in UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN
npm install
npm run dev
```
Open http://localhost:3000

## Deploy to Vercel
1. vercel.com → New Project → import `Tele-GoldBCH`
2. Set **Root Directory** to `mobile-dashboard`
3. Add env vars:
   - `UPSTASH_REDIS_REST_URL`
   - `UPSTASH_REDIS_REST_TOKEN`
4. Deploy — auto-rebuilds on every git push

## Architecture
- Python bot writes to Redis key `tele_goldbch:state` every scan cycle
- Next.js server route `/api/state` reads Redis (keeps token server-only)
- Client component polls `/api/state` every 10s, re-renders on change
