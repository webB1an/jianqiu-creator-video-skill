#!/usr/bin/env python3
"""Look up visual techniques in official technique and film-style catalogs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shlex
import sys
from tempfile import NamedTemporaryFile
import time
from unicodedata import normalize
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


ATLAS = "https://atlas.letz.ai/"
MOTION = "https://showheyohtaki.github.io/motion-prompt/"
REPO = "https://github.com/showheyohtaki/motion-prompt"
LEMO = "https://github.com/lemomo-ai/lemo-opuscar"
LEMO_SITE = "https://lemomo-ai.github.io/lemo-opuscar/"
SOURCES = {
    "atlas": {
        "name": "LetzAI Atlas of Visual Effects", "url": ATLAS,
        "documentation": ATLAS + "agents", "credits": ATLAS + "sources",
        "rights": "图鉴中的示例图逐项署名、授权。打开对应条目核实所用原件的权利。",
    },
    "lemo": {
        "name": "Lemo-Opuscar", "url": LEMO_SITE + "zh/", "author": "Lemomo (LemoLab)",
        "documentation": LEMO, "credits": LEMO + "/blob/main/LICENSE",
        "rights": "文档 CC BY-NC 4.0，作者另许可用于制作自己的电影（含客户及商业片）。文档的商业再分发等超出这项许可的用途需另行授权；示范影片与剧照 CC BY 4.0，代码按 MIT，第三方素材依各自 CREDITS。",
    },
    "motion": {
        "name": "Motion Prompt", "url": MOTION, "author": "大滝昇平 / Shohei Ohtaki (@showheyohtaki)",
        "documentation": REPO, "license_url": REPO + "/blob/main/LICENSE",
        "credits": REPO + "/blob/main/THIRD-PARTY-NOTICES.md",
        "rights": "作者的项目使用 MIT 许可。复用项目代码须保留版权与许可声明。示例页嵌入的第三方帖子由其作者持权。",
    },
}
# A small bridge from spoken Chinese to catalog terms. The catalogs supply the actual hits.
ALIASES = {
    "rack focus": ("焦点转移", "转移焦点", "移焦", "把焦点从前景拉到背景", "从前景把焦点拉到背景", "从前景对焦到背景", "前景变虚背景变清晰"),
    "rim light": ("轮廓光", "边缘光", "用光勾出轮廓", "用光勾出主体轮廓", "用光勾出人物轮廓"),
    "cyanotype": ("蓝晒", "氰版", "蓝晒法", "氰版印相"),
    "cyanotype simulation": ("数字蓝晒", "模拟蓝晒"),
    "dolly zoom": ("滑动变焦", "希区柯克变焦", "希区考克变焦", "主体大小不变背景被拉伸"),
    "double exposure": ("双重曝光", "二次曝光"),
    "parallax": ("视差", "让前后景以不同速度移动"),
    "overshoot": ("过冲回弹", "超过目标再回弹"),
    "match cut": ("匹配剪辑", "匹配剪接"),
    "halftone": ("半色调", "半色调网点"),
    "urban sketch": ("城市速写", "钢笔淡彩"),
    "shadow puppet": ("皮影", "皮影戏"),
    "ukiyoe": ("浮世绘",),
}


def normal(value):
    return " ".join(re.findall(r"[\w]+", normalize("NFKC", value).casefold())).strip()


def search_term(query):
    value = normal(query)
    matches = [(len(alias), term) for term, aliases in ALIASES.items()
               for alias in aliases if alias in value]
    # The more specific expression wins, e.g. 数字蓝晒 over 蓝晒.
    return max(matches)[1] if matches else query


def get_json(url):
    request = Request(url, headers={"User-Agent": "creator-video/1.0 (visual reference lookup)",
                                    "Accept": "application/json"})
    for attempt in (1, 2):
        try:
            with urlopen(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            if attempt == 2 or exc.code not in (502, 503, 504):
                raise
        except (URLError, HTTPException, OSError):
            if attempt == 2:
                raise


def valid_catalog(data, source):
    if source == "motion":
        rows = data.get("elements") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows or data.get("count") != len(rows):
            return False
        return all(isinstance(row, dict) and str(row.get("id", "")).isdigit() and
                   all(isinstance(row.get(k), str) and row[k] for k in ("slug", "pack")) and
                   all(isinstance(row.get(k), dict) and isinstance(row[k].get("en"), str)
                       for k in ("name", "meaning", "ask")) and isinstance(row.get("tags", []), list)
                   for row in rows)
    required = ("slug", "en", "cn", "cat", "cat_en", "film_cn")
    return isinstance(data, list) and bool(data) and all(
        isinstance(row, dict) and all(isinstance(row.get(k), str) for k in required) and
        bool(row["slug"]) and isinstance(row.get("aka", []), list) and isinstance(row.get("uses", []), list)
        for row in data)


def static_catalog(source, url):
    """Cache the publisher's two compact name indexes; never cache referenced media."""
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    key = hashlib.sha256(url.encode()).hexdigest()
    path = root / "creator-video" / "visual-sources" / (source + "-" + key + ".json")
    cached = None
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
            modified = os.fstat(stream.fileno()).st_mtime
        if valid_catalog(data, source):
            timestamp = datetime.fromtimestamp(modified, timezone.utc).isoformat(timespec="seconds")
            cached = data
            age = time.time() - modified
            if 0 <= age < 86400:
                return cached, {"catalog_origin": "cache_fresh", "cached_at": timestamp}
    except (OSError, ValueError, OverflowError):
        pass
    try:
        data = get_json(url)
        if not valid_catalog(data, source):
            raise ValueError("Official catalog has an unrecognized structure")
    except (URLError, HTTPException, OSError, ValueError) as exc:
        if cached is None:
            raise
        return cached, {"catalog_origin": "cache_stale", "cached_at": timestamp, "refresh_error": str(exc)}
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, separators=(",", ":"), ensure_ascii=False)
        os.replace(temporary, path)
    except OSError:
        # A read-only home directory still gets the live result.
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return data, {"catalog_origin": "network"}


def rank(query, term, names, tags=()):
    names = [normal(name) for name in names if name]
    tags = [normal(tag) for tag in tags]
    needles = {normal(query), normal(term)}
    if any(q == name for q in needles for name in names):
        return 0
    if any(q == tag for q in needles for tag in tags):
        return 1
    if any(q and q in name for q in needles for name in names):
        return 2
    return 3 if any(q and q in tag for q in needles for tag in tags) else 4


def motion_lookup(query, term, limit, detail):
    catalog_url = MOTION + "api/v1/catalog.json"
    catalog, origin = static_catalog("motion", catalog_url)
    rows = []
    for item in catalog["elements"]:
        names = item["name"]
        item_id = str(item["id"])
        grade = rank(query, term, [names.get("en"), names.get("zh-Hans"), item["slug"]],
                     item.get("tags", []))
        if re.fullmatch(r"#?\d{1,3}", query.strip()):
            grade = 0 if int(query.strip().lstrip("#")) == int(item_id) else 4
        if grade == 4:
            continue
        row = {"source": "motion", "id": item_id, "name": names["en"],
               "name_zh": names.get("zh-Hans", ""), "pack": item["pack"],
               "url": MOTION + "#" + item_id, "description": item["meaning"].get("zh-Hans") or item["meaning"]["en"],
               "reference_url": REPO + "/blob/main/packs/" + item["pack"] + "/README.zh-Hans.md"}
        if detail:
            row["prompt_example"] = item["ask"].get("zh-Hans") or item["ask"]["en"]
        rows.append((grade, row))
    rows.sort(key=lambda record: (record[0], record[1]["id"]))
    return rows[:limit], {"catalog_url": catalog_url, "catalog_version": catalog.get("version"),
                          "matched": len(rows), **origin}


def atlas_lookup(query, term, limit, detail):
    catalog_url = ATLAS + "api/public/atlas/catalog?" + urlencode({"q": term, "limit": limit})
    catalog = get_json(catalog_url)
    rows = []
    for item in catalog["effects"]:
        grade = rank(query, term, [item["title"], item["slug"]])
        row = {"source": "atlas", "id": item["slug"], "name": item["title"], "kind": item["kind"],
               "era": item["era"], "url": urljoin(ATLAS, item["article"]),
               "reference_url": urljoin(ATLAS, item["resource"])}
        row["description"] = ("历史技法 · " if item["kind"] == "historical" else "数字技法 · ") + item["era"]
        rows.append((grade, row))
    rows.sort(key=lambda record: record[0])
    meta = {"catalog_url": catalog_url, "matched": catalog["total"]}
    # One detail request identifies the leading candidate's visual signature and example credit.
    if detail and rows:
        top = rows[0][1]
        try:
            effect = get_json(top["reference_url"])["effect"]
            text = next((p["text"] for p in effect.get("body", []) if p.get("label") == "Identify"), None)
            if text:
                top["description"] = text
            if effect.get("credit"):
                top["example_credit"] = effect["credit"]
            if effect.get("sourceUrl"):
                top["example_source_url"] = effect["sourceUrl"]
        except (URLError, HTTPException, OSError, ValueError, KeyError, TypeError) as exc:
            meta["detail_error"] = str(exc)
    return rows, meta


def lemo_lookup(query, term, limit, detail):
    catalog_url = "https://raw.githubusercontent.com/lemomo-ai/lemo-opuscar/main/styleboard/catalog.json"
    catalog, origin = static_catalog("lemo", catalog_url)
    rows = []
    for item in catalog:
        slug = item["slug"]
        names = [item["cn"], item["en"], slug, *item.get("aka", [])]
        grade = rank(query, term, names, [item["cat"], item["cat_en"], *item.get("uses", [])])
        if grade == 4:
            continue
        row = {"source": "lemo", "id": slug, "name": item["en"], "name_zh": item["cn"],
               "url": LEMO_SITE + "zh/styles/" + slug + "/", "reference_url": LEMO + "/blob/main/styles/" + slug + "/STYLE.md",
               "description": item["cat"] + " · 官方样片《" + item["film_cn"] + "》"}
        if detail:
            row["demo_notes_url"] = LEMO + "/blob/main/styles/" + slug + "/DEMO.md"
            row["demo_credits_url"] = LEMO + "/blob/main/styles/" + slug + "/demo/CREDITS"
        contributor = {"memphis-diorama": "EianLu", "crayon-world": "2nd1st"}.get(slug)
        if contributor:
            row["author"] = contributor + " · Lemo-Opuscar / Lemomo (LemoLab)"
        rows.append((grade, row))
    rows.sort(key=lambda record: record[0])
    return rows[:limit], {"catalog_url": catalog_url, "matched": len(rows), **origin}


def lookup(query, source="all", limit=5, detail=False):
    selected = list(SOURCES) if source == "all" else [source]
    term = search_term(query)
    result = {"query": query, "search_term": term, "sources": {}, "results": []}
    matches = []
    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        readers = {"atlas": atlas_lookup, "motion": motion_lookup, "lemo": lemo_lookup}
        calls = {key: pool.submit(readers[key], query, term, limit, detail)
                 for key in selected}
        for key, job in calls.items():
            info = dict(SOURCES[key])
            try:
                rows, meta = job.result()
                matches.extend(rows)
                info.update(meta, status="ok")
            except (URLError, HTTPException, OSError, ValueError, KeyError, TypeError) as exc:
                info.update(status="unavailable", error=str(exc))
            result["sources"][key] = info
    matches.sort(key=lambda record: record[0])
    result["results"] = [row for _, row in matches[:limit]]
    if not matches or any(info.get("status") != "ok" or info.get("detail_error") or info.get("refresh_error")
                          for info in result["sources"].values()):
        result["local_search"] = " ".join(shlex.quote(part) for part in (sys.executable, str(Path(__file__).resolve().with_name("library.py")), "search", query))
    return result


def main():
    parser = argparse.ArgumentParser(description="查询官方图鉴的术语、实例页面与出处（只读）。")
    parser.add_argument("query", help="中文、英文术语或 Motion Prompt 编号，如 #033")
    parser.add_argument("--source", choices=("all", "atlas", "motion", "lemo"), default="all")
    parser.add_argument("--limit", type=int, default=5, help="最多返回的结果数，默认 5")
    parser.add_argument("--detail", action="store_true", help="附官网提示示例、首条 Atlas 图例信息或 Lemo 样片制作页")
    parser.add_argument("--json", action="store_true", help="输出适合分镜处理的 JSON")
    args = parser.parse_args()
    if not args.query.strip() or not 1 <= args.limit <= 100:
        parser.error("请输入检索词，结果数在 1–100 之间")
    data = lookup(args.query.strip(), args.source, args.limit, args.detail)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for row in data["results"]:
            name = " / ".join(filter(None, [row.get("name_zh"), row["name"]]))
            print(f"{name}   [{data['sources'][row['source']]['name']}]\n  {row['description']}\n  {row['url']}")
            if row.get("demo_notes_url"):
                print("  风格说明 ·", row["reference_url"])
                print("  样片制作说明 ·", row["demo_notes_url"])
                print("  样片用材署名 ·", row["demo_credits_url"])
            if row.get("author"):
                print("  风格作者 ·", row["author"])
            if row.get("prompt_example"):
                print("  官方提示示例 ·", row["prompt_example"])
            if row.get("example_credit"):
                print("  条目图例 ·", row["example_credit"], row.get("example_source_url", ""))
        if not data["results"]:
            print("未取得匹配条目。")
        for info in data["sources"].values():
            if info.get("catalog_origin") == "cache_stale":
                print(f"{info['name']} 在线更新失败，使用 {info['cached_at']} 的旧目录 · {info['refresh_error']} · {info['url']}")
            if info["status"] == "unavailable":
                print(f"{info['name']} 未响应 · {info['error']} · {info['url']}")
            elif info.get("detail_error"):
                print(f"{info['name']} 详情未取得 · {info['detail_error']} · {info['url']}")
            else:
                author = " · " + info["author"] if info.get("author") else ""
                print(f"{info['name']}{author} · {info['rights']} {info['credits']}")
        if "local_search" in data:
            print("个人库 ·", data["local_search"])
    return 0 if any(info["status"] == "ok" for info in data["sources"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
