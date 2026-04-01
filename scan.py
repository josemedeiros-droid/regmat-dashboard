"""
RegMat Scanner (GitHub Actions edition).
Reads Slack channels, detects @invops-regulatory-matters mentions,
and updates data.json in the repo.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

DATA_FILE = Path("data.json")

WATCH_CHANNELS = [
    {"id": "C0APAM34JT0", "name": "teste-regmat"},
    {"id": "C0AQCN18Q0L", "name": "teste-regmat2"},
    {"id": "C0AQCN3LYTE", "name": "teste-regmat3"},
]

USERGROUP_IDS = ["S0APXGLNL4C", "S0ANPHLR9DW"]
INTAKE_MARKER = "NOVA DEMANDA"

FIELD_PATTERNS = {
    "tipo_demanda":  r"Tipo:\s*\[?(.+?)[\]\n]",
    "origem":        r"Origem:\s*\[?(.+?)[\]\n]",
    "solicitante":   r"Solicitante:\s*(.+)",
    "prazo_formal":  r"Prazo formal:\s*(.+)",
    "criticidade":   r"Criticidade sugerida:\s*\[?(.+?)[\]\n]",
    "escopo":        r"Escopo resumido.*?:\n>\s*(.+(?:\n>.*)*)",
    "squads":        r"Squads/Distritos.*?:\n>\s*(.+(?:\n>.*)*)",
}


def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"demands": [], "last_scan": None, "scan_count": 0}


def save_data(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def mentions_group(text: str) -> bool:
    return any(f"<!subteam^{gid}>" in text for gid in USERGROUP_IDS)


def extract_fields(text: str) -> dict:
    fields = {}
    for key, pattern in FIELD_PATTERNS.items():
        m = re.search(pattern, text, re.MULTILINE)
        fields[key] = m.group(1).strip() if m else None
    return fields


def clean_preview(text: str) -> str:
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"[^\w\s.,;:!?()/-]", "", clean)
    return clean.strip()[:200]


def get_user_name(client: WebClient, user_id: str, cache: dict) -> str:
    if user_id in cache:
        return cache[user_id]
    try:
        info = client.users_info(user=user_id)
        name = info["user"]["real_name"]
    except SlackApiError:
        name = user_id
    cache[user_id] = name
    return name


def get_permalink(client: WebClient, channel_id: str, ts: str) -> str:
    try:
        return client.chat_getPermalink(channel=channel_id, message_ts=ts)["permalink"]
    except SlackApiError:
        return f"https://nubank.slack.com/archives/{channel_id}/p{ts.replace('.', '')}"


def next_protocol_id(existing_ids: list[str]) -> str:
    year = datetime.now().strftime("%Y")
    seq = 1
    while f"RM-{year}-{seq:03d}" in existing_ids:
        seq += 1
    return f"RM-{year}-{seq:03d}"


def main():
    token = os.environ.get("SLACK_TOKEN")
    if not token:
        print("SLACK_TOKEN not set")
        sys.exit(1)

    client = WebClient(token=token)

    try:
        auth = client.auth_test()
        print(f"Authenticated: {auth['user']} @ {auth['team']}")
    except SlackApiError as exc:
        print(f"Auth failed: {exc.response['error']}")
        sys.exit(1)

    data = load_data()
    existing_ts = {d.get("message_ts") for d in data["demands"]}
    existing_ids = [d["id"] for d in data["demands"]]
    user_cache: dict[str, str] = {}
    new_count = 0

    def _process(msg, channel_id, channel_name, is_thread=False):
        nonlocal new_count
        text = msg.get("text", "")
        if msg.get("subtype") or not mentions_group(text):
            return
        if msg["ts"] in existing_ts:
            return

        user_id = msg.get("user", "unknown")
        author = get_user_name(client, user_id, user_cache)
        permalink = get_permalink(client, channel_id, msg["ts"])
        ts_dt = datetime.fromtimestamp(float(msg["ts"]))
        protocol_id = next_protocol_id(existing_ids)
        existing_ids.append(protocol_id)
        source = f"#{channel_name}" + (" (thread)" if is_thread else "")

        if INTAKE_MARKER in text:
            fields = extract_fields(text)
            demand = {
                "id": protocol_id,
                "timestamp": ts_dt.strftime("%Y-%m-%d %H:%M"),
                "type": "demand",
                "from": author,
                "channel": channel_name,
                "criticality": fields.get("criticidade"),
                "due_date": fields.get("prazo_formal"),
                "summary": fields.get("escopo"),
                "preview": clean_preview(text),
                "status": "Novo",
                "permalink": permalink,
                "message_ts": msg["ts"],
                "fields": fields,
            }
        else:
            demand = {
                "id": protocol_id,
                "timestamp": ts_dt.strftime("%Y-%m-%d %H:%M"),
                "type": "mention",
                "from": author,
                "channel": channel_name,
                "criticality": None,
                "due_date": None,
                "summary": None,
                "preview": clean_preview(text),
                "status": "Novo",
                "permalink": permalink,
                "message_ts": msg["ts"],
            }

        data["demands"].insert(0, demand)
        existing_ts.add(msg["ts"])
        new_count += 1
        print(f"  + {demand['type']} from {author} in {source}")

    for ch in WATCH_CHANNELS:
        channel_id, channel_name = ch["id"], ch["name"]

        oldest = "0"
        for d in data["demands"]:
            if d.get("channel") == channel_name and d.get("message_ts"):
                ts_val = d["message_ts"]
                if ts_val > oldest:
                    oldest = ts_val

        try:
            result = client.conversations_history(
                channel=channel_id, oldest=oldest, limit=100, inclusive=False,
            )
        except SlackApiError as exc:
            print(f"  Error reading #{channel_name}: {exc.response['error']}")
            continue

        new_messages = result.get("messages", [])

        for msg in sorted(new_messages, key=lambda m: float(m["ts"])):
            _process(msg, channel_id, channel_name)

        try:
            recent = client.conversations_history(channel=channel_id, limit=30)
            recent_msgs = recent.get("messages", [])
        except SlackApiError:
            recent_msgs = new_messages

        for msg in recent_msgs:
            if msg.get("reply_count", 0) > 0:
                try:
                    replies = client.conversations_replies(
                        channel=channel_id, ts=msg["ts"], limit=200,
                    )
                    for reply in replies.get("messages", []):
                        if reply["ts"] == msg["ts"]:
                            continue
                        _process(reply, channel_id, channel_name, is_thread=True)
                except SlackApiError:
                    pass

    data["last_scan"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    data["scan_count"] = data.get("scan_count", 0) + 1
    save_data(data)

    print(f"Done: {new_count} new, {len(data['demands'])} total")


if __name__ == "__main__":
    main()
