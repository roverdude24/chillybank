"""ChillyBank: shape the book after the family's Notion Tài chính and categorize its money flow.

Run from the repo root with the app's env (`set -a; . ./.env; set +a; .venv/bin/python scripts/chilly_book_setup.py`).
Re-runnable: every step checks before it writes.

1. Removes the fresh-book starter envelopes, groups and categories nobody uses, so the budget is
   Notion's own groups (Thiết yếu, Nợ, Dự phòng, Công việc, Mục tiêu & đầu tư, Chờ phân loại).
2. Creates the income categories Notion tags rows with (Nguồn thu).
3. Adds payees with rules that route Vietnamese merchants, bills and transfers to Notion's
   envelopes, so statements, Telegram and email land in the right envelope with no review.
4. Gives Lucius read access to Notion (HOMETV bot key) plus "create a row", and his
   `ghi-giao-dich` skill for logging money into Notion Giao dịch.
5. Runs the Notion sync, which re-books Notion income under those categories.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from backend.core.errors import ConflictError
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import payee_registry, planning
from backend.services.notion_input import sync_notion_input

NOTION_READ_TOOLS = [
    "API-get-self", "API-post-search", "API-get-block-children", "API-retrieve-a-block",
    "API-retrieve-a-page", "API-retrieve-a-page-property", "API-retrieve-a-comment",
    "API-query-data-source", "API-retrieve-a-data-source", "API-retrieve-a-database",
    "API-list-data-source-templates", "API-retrieve-page-markdown",
]
SKILL = Path(__file__).with_name("chilly_skills") / "ghi-giao-dich.md"

STARTER_GROUPS = ("Bills", "Needs", "Wants")
INCOME = ("Lãi tiền gửi", "Lương Ánh", "Project Vinh", "Zenme 1%", "Thu nhập khác")

# (payee, envelope or income category, kind, regex on the raw bank text, lower-cased; accents optional)
PAYEES = [
    ("Grab", "Sinh hoạt & phát sinh", "expense", r"\bgrab"),
    ("Be", "Sinh hoạt & phát sinh", "expense", r"\bbe group|\bbe ?(car|bike|food)"),
    ("Xanh SM", "Sinh hoạt & phát sinh", "expense", r"xanh ?sm|gsm\b"),
    ("Shopee", "Sinh hoạt & phát sinh", "expense", r"shopee(?!pay)"),
    ("Lazada", "Sinh hoạt & phát sinh", "expense", r"lazada"),
    ("Tiki", "Sinh hoạt & phát sinh", "expense", r"\btiki\b"),
    ("TikTok Shop", "Sinh hoạt & phát sinh", "expense", r"tiktok"),
    ("Circle K", "Sinh hoạt & phát sinh", "expense", r"circle ?k"),
    ("GS25", "Sinh hoạt & phát sinh", "expense", r"gs25"),
    ("WinMart", "Sinh hoạt & phát sinh", "expense", r"winmart|vinmart"),
    ("Bách Hóa Xanh", "Sinh hoạt & phát sinh", "expense", r"b[aá]ch h[oó]a xanh|\bbhx\b"),
    ("Co.opmart", "Sinh hoạt & phát sinh", "expense", r"co\.?op ?(mart|food|xtra)"),
    ("Highlands Coffee", "Sinh hoạt & phát sinh", "expense", r"highlands"),
    ("Phúc Long", "Sinh hoạt & phát sinh", "expense", r"ph[uú]c long"),
    ("Starbucks", "Sinh hoạt & phát sinh", "expense", r"starbucks"),
    ("Long Châu", "Sinh hoạt & phát sinh", "expense", r"long ch[aâ]u"),
    ("Pharmacity", "Sinh hoạt & phát sinh", "expense", r"pharmacity"),
    ("Tiền điện EVN", "Sinh hoạt & phát sinh", "expense", r"\bevn|ti[eề]n [dđ]i[eệ]n"),
    ("Tiền nước", "Sinh hoạt & phát sinh", "expense", r"sawaco|ti[eề]n n[uư][oớ]c|c[aấ]p n[uư][oớ]c"),
    ("Internet", "Sinh hoạt & phát sinh", "expense", r"fpt telecom|vnpt|viettel.*(internet|cuoc|cước)"),
    ("Thuế đất", "Sinh hoạt & phát sinh", "expense", r"thu[eế] [dđ][aấ]t"),
    ("Claude / Anthropic", "Công việc & công cụ AI", "expense", r"anthropic|claude"),
    ("OpenAI", "Công việc & công cụ AI", "expense", r"openai|chatgpt"),
    ("Cursor", "Công việc & công cụ AI", "expense", r"cursor"),
    ("Runway", "Công việc & công cụ AI", "expense", r"runway"),
    ("Midjourney", "Công việc & công cụ AI", "expense", r"midjourney"),
    ("GitHub", "Công việc & công cụ AI", "expense", r"github"),
    ("Học phí", "Học phí các con", "expense", r"h[oọ]c ph[ií]|\bemg\b|m[aầ]m non"),
    ("Tiền thuê nhà", "Tiền thuê nhà", "expense", r"ti[eề]n (thu[eê]|nh[aà])|thu[eê] nh[aà]"),
    ("Trả thẻ tín dụng", "Trả nợ tín dụng", "expense", r"tr[aả] (th[eẻ]|n[oợ])|sao k[eê]|v[ií] tr[aả] sau|tnex"),
    ("Lãi tiền gửi", "Lãi tiền gửi", "income", r"^l[aã]i\b|tra lai|ti[eề]n l[aã]i|interest"),
]


def tidy_starters() -> list[str]:
    done = []
    groups = {g["id"]: g["name"] for g in planning.list_groups()}
    for env in envelopes_service.all_envelopes():
        if env.get("archived") or groups.get(env.get("group_id")) not in STARTER_GROUPS:
            continue
        if any(categories_service.category_in_use(c) for c in env.get("category_ids") or []):
            continue  # someone booked money here: keep it
        planning.apply_budget_change({"type": "envelope_delete", "envelope_id": env["id"]})
        done.append(f"envelope {env['name']}")
        for cid in env.get("category_ids") or []:
            try:
                categories_service.delete_category(cid)
                done.append(f"category {cid}")
            except ConflictError:
                pass
    for gid, name in groups.items():
        if name in STARTER_GROUPS and not any(e.get("group_id") == gid for e in envelopes_service.all_envelopes()):
            planning.apply_budget_change({"type": "group_delete", "group_id": gid})
            done.append(f"group {name}")
    return done


def category(label: str, kind: str) -> str:
    return categories_service.resolve_category_ref(label) or categories_service.create_category(label, kind)["id"]


def payees() -> list[str]:
    done = []
    names = {p["name"]: p for p in payee_registry.list_registry()}
    for name, label, kind, regex in PAYEES:
        cid = categories_service.resolve_category_ref(label)
        if cid is None:
            done.append(f"skip {name}: no category {label!r}")
            continue
        payee = names.get(name) or payee_registry.create_payee(name)
        payee_registry.set_default_categories(payee["id"], {kind: cid})
        if not any(r.get("value") == regex for r in payee.get("rules") or []):
            payee_registry.add_payee_rule(payee["id"], "regex", regex)
            done.append(f"payee {name} -> {label}")
    return done


def notion_mcp() -> str:
    """Lucius's Notion tools: reads run without a card (a card per lookup made chat unusable);
    creating a row always asks."""
    from backend.mcp.client_manager import mcp_connector_id
    from backend.services import tool_permissions
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    token = os.getenv("NOTION_TOKEN")
    if not token:
        return "skip: no NOTION_TOKEN"
    store = McpClientStore()
    fields = {
        "name": "Notion (Tài chính)",
        "transport": "stdio",
        "command": "/opt/homebrew/bin/npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env": {"NOTION_TOKEN": token, "PATH": "/opt/homebrew/bin:/usr/bin:/bin"},
        "enabled": True,
        "read_only": False,
        "tool_allowlist": [*NOTION_READ_TOOLS, "API-post-page"],
    }
    outcome = "updated" if store.find("notion") else "created"
    if outcome == "updated":
        store.update("notion", fields)
    else:
        store.create(McpServerConfig(id="notion", **fields))
    owner = mcp_connector_id("notion")
    for tool in NOTION_READ_TOOLS:
        tool_permissions.set_tool_permission(owner, tool, "always_allow")
    tool_permissions.set_tool_permission(owner, "API-post-page", "needs_approval")
    return outcome


def lucius_skill() -> str:
    from backend.ai.tools.registry import dispatch
    from backend.db.skill_store import SkillStore

    body = SKILL.read_text(encoding="utf-8")
    description = "Ghi khoản chi/thu/chuyển tiền của gia đình vào Notion Giao dịch (sổ chính), ChillyBank tự đồng bộ."
    if any(s.get("id") == "ghi-giao-dich" for s in SkillStore().all()):
        call = ("update_skill", {"skill_id": "ghi-giao-dich", "changes": {"body": body, "description": description}})
    else:
        call = ("create_skill", {"name": "ghi-giao-dich", "description": description, "body": body})
    text, _ = asyncio.run(dispatch(*call))
    return json.loads(text).get("ok") and call[0]


def main() -> None:
    report = {"tidied": tidy_starters(), "income": [category(c, "income") and c for c in INCOME], "payees": payees()}
    report["notion_mcp"] = notion_mcp()
    report["skill"] = lucius_skill()
    report["notion"] = asyncio.run(sync_notion_input())
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
