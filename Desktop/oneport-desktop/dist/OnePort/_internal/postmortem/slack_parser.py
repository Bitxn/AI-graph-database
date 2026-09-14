import json
from datetime import datetime


def parse_slack_export(filepath: str) -> str:
    """
    Parses a Slack JSON export into a readable incident thread.
    
    Slack exports are either:
    - A single JSON array of messages (one channel export)
    - The format you get from Slack's "Export" or third-party tools
    """
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Handle both array format and dict with "messages" key
    if isinstance(data, dict):
        messages = data.get("messages", [])
    elif isinstance(data, list):
        messages = data
    else:
        raise ValueError("Unrecognised Slack JSON format. Expected a list or dict with 'messages' key.")

    if not messages:
        raise ValueError("No messages found in Slack export.")

    lines = ["SLACK INCIDENT THREAD\n" + "=" * 40]

    for msg in messages:
        # Skip bot join/leave noise
        msg_type = msg.get("type", "")
        subtype = msg.get("subtype", "")
        if subtype in ("channel_join", "channel_leave", "bot_message" ):
            continue

        # Timestamp
        ts = msg.get("ts", "")
        try:
            dt = datetime.fromtimestamp(float(ts))
            time_str = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            time_str = ts or "??"

        # Username
        user = msg.get("user_profile", {}).get("display_name") \
            or msg.get("username") \
            or msg.get("user") \
            or "unknown"

        # Message text
        text = msg.get("text", "").strip()
        if not text:
            continue

        # Clean up Slack's user mention format <@U12345>
        import re
        text = re.sub(r"<@[A-Z0-9]+>", "@user", text)
        text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)
        text = re.sub(r"<(https?://[^|>]+)\|?[^>]*>", r"\1", text)

        lines.append(f"[{time_str}] {user}: {text}")

        # Include thread replies if present
        for reply in msg.get("replies", []):
            reply_text = reply.get("text", "").strip()
            reply_user = reply.get("user_profile", {}).get("display_name") or reply.get("user", "unknown")
            reply_ts = reply.get("ts", "")
            try:
                reply_dt = datetime.fromtimestamp(float(reply_ts))
                reply_time = reply_dt.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                reply_time = "??"
            if reply_text:
                lines.append(f"  ↳ [{reply_time}] {reply_user}: {reply_text}")

    lines.append("=" * 40)
    return "\n".join(lines)