import os
import re
from datetime import datetime
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

SEV_COLORS = {
    "CRITICAL": "#FF4444",
    "HIGH":     "#FF8C00",
    "MEDIUM":   "#00AEEF",
    "LOW":      "#00C851",
}

PRIORITY_COLORS = {
    "P1": "#FF4444",
    "P2": "#FF8C00",
    "P3": "#00AEEF",
}


def _get_config() -> dict:
    config = {
        "base_url":       os.getenv("CONFLUENCE_BASE_URL", "").rstrip("/"),
        "email":          os.getenv("CONFLUENCE_EMAIL", ""),
        "api_token":      os.getenv("CONFLUENCE_API_TOKEN", ""),
        "space_key":      os.getenv("CONFLUENCE_SPACE_KEY", "ENG"),
        "parent_page_id": os.getenv("CONFLUENCE_PARENT_PAGE_ID", ""),
    }
    missing = [k for k, v in config.items() if not v]
    if missing:
        raise ValueError(
            f"Missing Confluence config in .env: {', '.join(missing).upper()}"
        )
    return config


def _extract_field(text: str, field: str) -> str:
    match = re.search(rf"^{field}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_section_text(text: str, section: str) -> str:
    pattern = rf"{section}[:\s]*\n(.*?)(?=\n[A-Z ]+[:\(]|\Z)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_timeline(text: str) -> list[str]:
    lines = []
    in_section = False
    for line in text.split("\n"):
        if "TIMELINE" in line.upper():
            in_section = True
            continue
        if in_section:
            if any(line.strip().startswith(s) for s in (
                "ROOT", "CONTRIBUTING", "ACTION", "LESSONS", "SUMMARY"
            )):
                break
            if re.match(r"\s*\[?\d{2}:\d{2}", line):
                lines.append(line.strip())
    return lines


def _extract_root_cause_chain(text: str) -> list[str]:
    lines = []
    in_section = False
    for line in text.split("\n"):
        if "ROOT CAUSE" in line.upper():
            in_section = True
            continue
        if in_section:
            if any(line.strip().startswith(s) for s in (
                "CONTRIBUTING", "ACTION", "LESSONS", "SUMMARY", "TIMELINE"
            )):
                break
            if "→" in line and line.strip():
                lines.append(line.strip())
    return lines


def _extract_bullets(text: str, section: str) -> list[str]:
    body = _extract_section_text(text, section)
    return [
        line.strip().lstrip("•").strip()
        for line in body.split("\n")
        if line.strip().startswith("•")
    ]


def _extract_action_items(text: str) -> list[dict]:
    items = []
    lines = text.split("\n")
    in_section = False
    current = None
    for line in lines:
        if "ACTION ITEMS" in line.upper():
            in_section = True
            continue
        if in_section:
            if any(line.strip().startswith(s) for s in (
                "LESSONS", "CONTRIBUTING", "SUMMARY", "TIMELINE", "ROOT CAUSE"
            )):
                break
            match = re.match(r"\[(P[123])\]\s+(.+)", line.strip())
            if match:
                if current:
                    items.append(current)
                current = {
                    "priority":    match.group(1),
                    "description": match.group(2).strip(),
                    "owner": "", "due_date": "",
                }
            elif current and line.strip().startswith("Owner:"):
                owner_m = re.search(r"Owner:\s*([^·]+)", line)
                due_m   = re.search(r"Due:\s*(.+)$", line)
                if owner_m: current["owner"]    = owner_m.group(1).strip()
                if due_m:   current["due_date"] = due_m.group(1).strip()
    if current:
        items.append(current)
    return items


def _build_confluence_storage(raw_text: str) -> str:
    """
    Converts a raw post-mortem text into Confluence Storage Format (XHTML).
    This is what Confluence actually renders as a page.
    """
    title       = _extract_field(raw_text, "TITLE")
    date        = _extract_field(raw_text, "DATE")
    duration    = _extract_field(raw_text, "DURATION")
    severity    = _extract_field(raw_text, "SEVERITY").upper()
    impact      = _extract_field(raw_text, "IMPACT")
    summary     = _extract_section_text(raw_text, "SUMMARY")
    lessons     = _extract_section_text(raw_text, "LESSONS LEARNED")
    timeline    = _extract_timeline(raw_text)
    why_chain   = _extract_root_cause_chain(raw_text)
    factors     = _extract_bullets(raw_text, "CONTRIBUTING FACTORS")
    actions     = _extract_action_items(raw_text)

    sev_color   = SEV_COLORS.get(severity, "#888888")
    generated   = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    # ── Info panel macro ──────────────────────────────────────────────────────
    def info_macro(body: str, macro_type: str = "info") -> str:
        return f"""
<ac:structured-macro ac:name="{macro_type}">
  <ac:rich-text-body>{body}</ac:rich-text-body>
</ac:structured-macro>"""

    # ── Section heading ───────────────────────────────────────────────────────
    def h2(text: str) -> str:
        return f"<h2>{text}</h2>"

    # ── Build action items table ──────────────────────────────────────────────
    action_rows = ""
    for item in actions:
        p_color = PRIORITY_COLORS.get(item["priority"], "#888")
        action_rows += f"""
<tr>
  <td><strong><span style="color:{p_color};">{item['priority']}</span></strong></td>
  <td>{item['description']}</td>
  <td>{item['owner'] or '—'}</td>
  <td>{item['due_date'] or '—'}</td>
</tr>"""

    actions_table = f"""
<table>
  <thead>
    <tr>
      <th>Priority</th>
      <th>Action Item</th>
      <th>Owner</th>
      <th>Due Date</th>
    </tr>
  </thead>
  <tbody>{action_rows}</tbody>
</table>""" if actions else "<p>No action items recorded.</p>"

    # ── Build timeline list ───────────────────────────────────────────────────
    timeline_items = "".join(f"<li><code>{line}</code></li>" for line in timeline)
    timeline_html  = f"<ul>{timeline_items}</ul>" if timeline_items else "<p>No timeline recorded.</p>"

    # ── Build 5-whys list ─────────────────────────────────────────────────────
    why_items = "".join(f"<li>{line}</li>" for line in why_chain)
    why_html  = f"<ol>{why_items}</ol>" if why_items else "<p>No root cause chain recorded.</p>"

    # ── Build contributing factors ────────────────────────────────────────────
    factor_items = "".join(f"<li>{f}</li>" for f in factors)
    factors_html = f"<ul>{factor_items}</ul>" if factor_items else "<p>None recorded.</p>"

    # ── Assemble the full page ────────────────────────────────────────────────
    page = f"""
<p>
  <em>Generated by oneport-postmortem · {generated}</em>
</p>

<table>
  <tbody>
    <tr>
      <th>Date</th><td>{date}</td>
      <th>Duration</th><td>{duration}</td>
    </tr>
    <tr>
      <th>Severity</th>
      <td><strong><span style="color:{sev_color};">{severity}</span></strong></td>
      <th>Impact</th><td>{impact}</td>
    </tr>
  </tbody>
</table>

{info_macro(f"<p>{summary}</p>", "info")}

{h2("Timeline")}
{timeline_html}

{h2("Root Cause (5-Whys)")}
{why_html}

{h2("Contributing Factors")}
{factors_html}

{h2("Action Items")}
{actions_table}

{h2("Lessons Learned")}
<p>{lessons}</p>

<hr />
{info_macro(
    f"<p>This post-mortem was auto-generated by <strong>oneport-postmortem</strong>. "
    f"All action items should be tracked in Jira. "
    f"For questions contact the on-call SRE team.</p>",
    "note"
)}
"""
    return page.strip()


def publish_to_confluence(raw_text: str) -> dict:
    """
    Creates a new Confluence page with the post-mortem content.
    Returns the page URL and ID on success.
    """
    config = _get_config()
    auth   = HTTPBasicAuth(config["email"], config["api_token"])
    headers = {
        "Accept":       "application/json",
        "Content-Type": "application/json",
    }

    title    = _extract_field(raw_text, "TITLE") or "Incident Post-Mortem"
    date     = _extract_field(raw_text, "DATE")  or datetime.now().strftime("%Y-%m-%d")
    severity = _extract_field(raw_text, "SEVERITY") or "UNKNOWN"

    page_title   = f"[Post-Mortem] {date} — {title} ({severity})"
    storage_body = _build_confluence_storage(raw_text)

    payload = {
        "type":  "page",
        "title": page_title,
        "space": {"key": config["space_key"]},
        "body":  {
            "storage": {
                "value":          storage_body,
                "representation": "storage",
            }
        },
    }

    # Attach to parent page if configured
    if config["parent_page_id"]:
        payload["ancestors"] = [{"id": config["parent_page_id"]}]

    response = requests.post(
        f"{config['base_url']}/rest/api/content",
        json=payload,
        headers=headers,
        auth=auth,
        timeout=15,
    )

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        try:
            detail = response.json()
            msg = detail.get("message") or detail.get("errorMessages", [str(response.status_code)])[0]
        except Exception:
            msg = f"HTTP {response.status_code}"
        raise ValueError(f"Confluence API error: {msg}")

    data     = response.json()
    page_id  = data.get("id")
    page_url = f"{config['base_url']}/pages/{page_id}"

    return {
        "page_id":    page_id,
        "page_url":   page_url,
        "page_title": page_title,
        "space_key":  config["space_key"],
    }


def test_confluence_connection() -> tuple[bool, str]:
    """Tests Confluence credentials. Returns (success, message)."""
    try:
        config  = _get_config()
        auth    = HTTPBasicAuth(config["email"], config["api_token"])
        headers = {"Accept": "application/json"}

        response = requests.get(
            f"{config['base_url']}/rest/api/space/{config['space_key']}",
            headers=headers,
            auth=auth,
            timeout=8,
        )
        response.raise_for_status()
        name = response.json().get("name", config["space_key"])
        return True, f"Connected to space: {name}"

    except requests.exceptions.HTTPError:
        return False, "Auth failed — check CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN in .env"
    except requests.exceptions.ConnectionError:
        return False, "Could not reach Confluence — check CONFLUENCE_BASE_URL in .env"
    except ValueError as e:
        return False, str(e)