# MAX transport smoke

`tools/max_smoke.py` turns the remaining live MAX transport checks into an
explicit, repeatable, values-free procedure. Its default `plan` mode never
contacts MAX. Every network mode refuses to run without
`--approve-external-action`.

Prepare the protected runtime env with `MAX_ENABLED=1`, `MAX_BOT_TOKEN`, and the
same `MAX_API_BASE_URL` / `MAX_CA_BUNDLE` used by the service. Keep target ids in
the ignored env or pass them as arguments; never commit them:

```dotenv
MAX_SMOKE_USER_ID=
MAX_SMOKE_CHAT_ID=
```

Offline validation:

```bash
python tools/max_smoke.py --mode plan --env-file .env
```

Operator-approved live sequence:

```bash
python tools/max_smoke.py --mode subscription --env-file .env --approve-external-action
python tools/max_smoke.py --mode message --env-file .env --approve-external-action
python tools/max_smoke.py --mode media --env-file .env --media-file ./safe-smoke.png --approve-external-action
```

- `subscription` performs one read of the current webhook subscriptions.
- `message` sends one fixed harmless message to `MAX_SMOKE_USER_ID`.
- `media` uploads the explicitly selected local image and sends it to
  `MAX_SMOKE_CHAT_ID`, exercising `/uploads`, multipart transfer, attachment
  readiness retry, and `/messages`.

Output contains only the mode result or exception class. It never prints the
bot token, target ids, provider response body, upload URL, or attachment token.
The message and media modes visibly mutate the target chat; use only an
operator-owned smoke conversation.
