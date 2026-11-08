"""ChillyBank: shape the book after the family's Notion Tài chính and categorize its money flow.

Run from the repo root with the app's env (`set -a; . ./.env; set +a; .venv/bin/python scripts/chilly_book_setup.py`).
Re-runnable: every step checks before it writes.

1. Removes the fresh-book starter envelopes, groups and categories nobody uses, so the budget is
   Notion's own groups (Thiết yếu, Nợ, Dự phòng, Công việc, Mục tiêu & đầu tư, Chờ phân loại).
2. Creates the income categories Notion tags rows with (Nguồn thu).
3. Adds payees with rules that route Vietnamese merchants, bills and transfers to Notion's
   envelopes, so statements, Telegram and email land in the right envelope with no review.
4. Runs the Notion sync, which re-books Notion income under those categories.
"""

from __future__ import annotations

import asyncio
import json

from backend.core.errors import ConflictError
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import payee_registry, planning
from backend.services.notion_input import sync_notion_input

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


def main() -> None:
    report = {"tidied": tidy_starters(), "income": [category(c, "income") and c for c in INCOME], "payees": payees()}
    report["notion"] = asyncio.run(sync_notion_input())
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
