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

ORGS: list[str] = [
    o.strip() for o in os.getenv("ORG", "flavortown").split(",") if o.strip()
]


def _transactions_file(org: str) -> str:
    return f"transactions_{org}.json"


def _load_transactions(org: str) -> list[dict]:
    path = _transactions_file(org)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w") as f:
            json.dump([], f)
        return []
    return json.load(open(path, "r"))


TRANSACTIONS: dict[str, list[dict]] = {org: _load_transactions(org) for org in ORGS}


async def _get_transactions(org: str) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://hcb.hackclub.com/api/v3/organizations/{org}/transactions"
        ) as resp:
            data = await resp.json()
            return data


async def _send_transaction(t: dict, org: str):
    memo = t.get("memo") or "—"
    amount_cents = t.get("amount_cents", 0)
    amount = amount_cents / 100
    date = t.get("date", "")
    color = "#2eb886" if amount_cents >= 0 else "#e01e5a"
    tid = t.get("id", "")
    href = (
        f"https://hcb.hackclub.com/hcb/{tid.split('_')[1]}"
        if tid
        else f"https://hcb.hackclub.com/{org}"
    )
    hcbscan_href = f"https://hcbscan.3kh0.net/app/txn/{tid}" if tid else None
    trans_type = t.get("type", "").upper().replace("_", " ")  # type: ignore
    user = t.get("user", {}).get("full_name", "???")
    imgfail = "https://images.rawpixel.com/image_png_800/cHJpdmF0ZS9sci9pbWFnZXMvd2Vic2l0ZS8yMDI0LTA0L3JtMTg4OS1lbGVtZW50LXotMzEucG5n.png"

    is_supa_mega = amount_cents < 0 and abs(amount_cents) > 4_000_000
    is_mega = amount_cents < 0 and abs(amount_cents) > 1_000_000

    if is_supa_mega:
        header_text = f"*NEW {trans_type} - SUPA MEGA PURCHASE* - {date}"
    elif is_mega:
        header_text = f"*NEW {trans_type} - MEGA PURCHASE* - {date}"
    else:
        header_text = f"*NEW {trans_type}* - {date}"

    context_text = (
        f"By: *{user}* | <{href}|hcb> | <{hcbscan_href}|hcbscan> | org: *{org}*"
    )
    if is_supa_mega:
        context_text += " | pinging <!channel>"

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": header_text},
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
                    "text": context_text,
                },
            ],
        },
    ]

    await client.chat_postMessage(
        channel=CHANNEL_ID,
        attachments=[{"color": color, "blocks": blocks}],
    )


async def update_transactions(org: str):
    txns = TRANSACTIONS[org]
    tres = await _get_transactions(org)
    if not tres:
        return

    if not txns:
        txns.extend(tres)
        json.dump(txns, open(_transactions_file(org), "w"), indent=2)
        # return

    new_items: list[dict] = []
    for t in tres:
        if t["id"] == txns[0]["id"]:
            break
        new_items.append(t)

    for t in reversed(new_items):
        txns.insert(0, t)
        await _send_transaction(t, org)

    with open(_transactions_file(org), "w") as f:
        json.dump(txns, f, indent=2)


async def force_refresh_and_send_last(org: str):
    txns = TRANSACTIONS[org]
    tres = await _get_transactions(org)
    if not tres:
        return

    txns.clear()
    txns.extend(tres)
    with open(_transactions_file(org), "w") as f:
        json.dump(txns, f, indent=2)

    await _send_transaction(txns[0], org)


async def poll_loop(interval_seconds: int = 10):
    while True:
        try:
            for org in ORGS:
                await update_transactions(org)
        except Exception as exc:
            print(f"poll_loop error: {exc}")
        await asyncio.sleep(interval_seconds)


async def handle_go(_request: web.Request) -> web.Response:
    try:
        for org in ORGS:
            await force_refresh_and_send_last(org)
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
