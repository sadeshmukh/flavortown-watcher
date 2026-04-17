import asyncio
import json
import os

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient


def _require_env(var) -> str:
    value = os.getenv(var)
    if not value:
        raise EnvironmentError(f"Required environment variable not set: {var}")
    return value


load_dotenv()

SLACK_BOT_TOKEN = _require_env("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = _require_env("SLACK_APP_TOKEN")
CHANNEL_ID = _require_env("CHANNEL_ID")

app = AsyncApp(token=SLACK_BOT_TOKEN)
client = AsyncWebClient(token=SLACK_BOT_TOKEN)

"""
transaction response schema parts that matter:
[{
id, object, href, amount_cents (can be negative), memo, date, comments (??), tags
},]
"""

if not os.path.exists("transactions.json"):
    with open("transactions.json", "w") as f:
        json.dump([], f)

if os.path.getsize("transactions.json") == 0:
    with open("transactions.json", "w") as f:
        json.dump([], f)

TRANSACTIONS: list[dict] = json.load(open("transactions.json", "r"))

ORG = os.getenv("ORG", "flavortown")


async def _get_transactions() -> list[dict]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://hcb.hackclub.com/api/v3/organizations/{ORG}/transactions"
        ) as resp:
            data = await resp.json()
            return data


async def _send_transaction(t: dict):
    memo = t.get("memo") or "—"
    amount_cents = t.get("amount_cents", 0)
    amount = amount_cents / 100
    date = t.get("date", "")
    color = "#2eb886" if amount_cents >= 0 else "#e01e5a"
    tid = t.get("id", "")
    href = (
        f"https://hcb.hackclub.com/hcb/{tid.split('_')[1]}"
        if tid
        else f"https://hcb.hackclub.com/{ORG}"
    )
    hcbscan_href = f"https://hcbscan.3kh0.net/app/txn/{tid}" if tid else None
    trans_type = t.get("type", "").upper().replace("_", " ")  # type: ignore
    user = t.get("user", {}).get("full_name", "???")
    imgfail = "https://images.rawpixel.com/image_png_800/cHJpdmF0ZS9sci9pbWFnZXMvd2Vic2l0ZS8yMDI0LTA0L3JtMTg4OS1lbGVtZW50LXotMzEucG5n.png"

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*NEW {trans_type}* - {date}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Memo*\n{memo}"},
                {"type": "mrkdwn", "text": f"*Balance change*\n${amount:+,.2f}"},
                # tags are added later usually? might figure that out but will require more reworking unfortunately
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "image",
                    "image_url": t.get("user", {}).get("photo", imgfail),
                    "alt_text": "user pfp",
                },
                {
                    "type": "mrkdwn",
                    "text": f"By: *{user}* | <{href}|hcb> | <{hcbscan_href}|hcbscan>",
                },
            ],
        },
    ]

    await client.chat_postMessage(
        channel=CHANNEL_ID,
        attachments=[{"color": color, "blocks": blocks}],
    )


async def update_transactions():
    tres = await _get_transactions()
    if not tres:
        return

    if not TRANSACTIONS:
        TRANSACTIONS.extend(tres)
        json.dump(TRANSACTIONS, open("transactions.json", "w"), indent=2)
        # return

    new_items: list[dict] = []
    for t in tres:
        if t["id"] == TRANSACTIONS[0]["id"]:
            break
        new_items.append(t)

    for t in reversed(new_items):
        TRANSACTIONS.insert(0, t)
        await _send_transaction(t)

    with open("transactions.json", "w") as f:
        json.dump(TRANSACTIONS, f, indent=2)


async def force_refresh_and_send_last():
    tres = await _get_transactions()
    if not tres:
        return

    TRANSACTIONS.clear()
    TRANSACTIONS.extend(tres)
    with open("transactions.json", "w") as f:
        json.dump(TRANSACTIONS, f, indent=2)

    await _send_transaction(TRANSACTIONS[0])


async def poll_loop(interval_seconds: int = 10):
    while True:
        try:
            await update_transactions()
        except Exception as exc:
            print(f"poll_loop error: {exc}")
        await asyncio.sleep(interval_seconds)


async def handle_go(_request: web.Request) -> web.Response:
    try:
        await force_refresh_and_send_last()
        return web.Response(text="ok")
    except Exception as exc:
        return web.Response(status=500, text=str(exc))


async def main():
    handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
    asyncio.create_task(poll_loop())

    web_app = web.Application()
    web_app.router.add_get("/go", handle_go)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 3000).start()
    print("Web server listening on :3000")

    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
