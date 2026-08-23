#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_gallery.py — 同步 photographs/ 文件夹和 photography.html 里的 PHOTOS 数组。

用法：
    python update_gallery.py            # 同步并写回文件
    python update_gallery.py --dry-run  # 只看会改什么，不写文件

它做三件事：
  1. 已在数组里、文件也还在  -> 原样保留（你写的标题、说明、顺序都不动）
  2. 文件夹里新增的图片      -> 追加到数组末尾，标题填占位符 TODO
  3. 数组里有、文件却没了    -> 从数组里删掉，并在终端提示

跑完之后，去 photography.html 里搜 "TODO"，把占位标题换成真的标题就行。

注意：脚本靠 photography.html 里的 /* PHOTOS:BEGIN */ 和 /* PHOTOS:END */
两行注释定位，别把它们删掉。
"""

import argparse
import re
import sys
from pathlib import Path

# Windows 控制台默认不是 UTF-8，不改的话打印中文会 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT      = Path(__file__).resolve().parent
HTML      = ROOT / "photography.html"
PHOTO_DIR = ROOT / "photographs"

# 只认这些后缀；photographs/ 下的子文件夹（比如 candidates/）不会被扫描
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

BEGIN = "/* PHOTOS:BEGIN */"
END   = "/* PHOTOS:END */"

# 匹配数组里每一个 { ... } 条目（条目内部没有嵌套花括号，所以这样就够）
OBJ_RE = re.compile(r"\{[^{}]*\}", re.S)


def js_escape(s: str) -> str:
    """把字符串安全地放进 JS 双引号里。"""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def read_field(block: str, key: str) -> str:
    """从一个 { ... } 文本块里取出 key:"value"，支持单双引号和转义。"""
    m = re.search(
        r'\b' + key + r'\s*:\s*(["\'])((?:[^\\]|\\.)*?)\1',
        block, re.S)
    if not m:
        return ""
    raw = m.group(2)
    return raw.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


def parse_existing(js: str):
    """把 PHOTOS 数组解析成 [{file,title,zh,cap,zhCap}, ...]，保持原顺序。"""
    entries = []
    for block in OBJ_RE.findall(js):
        f = read_field(block, "file")
        if not f:
            continue
        entries.append({
            "file":  f,
            "title": read_field(block, "title"),
            "zh":    read_field(block, "zh"),
            "cap":   read_field(block, "cap"),
            "zhCap": read_field(block, "zhCap"),
        })
    return entries


def render(entries) -> str:
    """把条目列表渲染回 JS 数组文本。"""
    out = ["const PHOTOS = ["]
    for i, e in enumerate(entries):
        tail = "" if i == len(entries) - 1 else ","
        out.append('  { file:"%s",' % js_escape(e["file"]))
        out.append('    title:"%s", zh:"%s",' % (js_escape(e["title"]), js_escape(e["zh"])))
        out.append('    cap:"%s",' % js_escape(e["cap"]))
        out.append('    zhCap:"%s" }%s' % (js_escape(e["zhCap"]), tail))
        if tail:
            out.append("")
    out.append("];")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    args = ap.parse_args()

    if not HTML.exists():
        print("找不到 %s" % HTML, file=sys.stderr)
        return 1
    if not PHOTO_DIR.is_dir():
        print("找不到 %s" % PHOTO_DIR, file=sys.stderr)
        return 1

    html = HTML.read_text(encoding="utf-8")
    if BEGIN not in html or END not in html:
        print("photography.html 里找不到 PHOTOS:BEGIN / PHOTOS:END 标记。", file=sys.stderr)
        return 1

    head, rest = html.split(BEGIN, 1)
    body, tail = rest.split(END, 1)

    existing = parse_existing(body)

    # 磁盘上实际有的图片（只扫一层，忽略 candidates/ 等子目录）
    on_disk = sorted(
        (p.name for p in PHOTO_DIR.iterdir()
         if p.is_file() and p.suffix.lower() in EXTS),
        key=str.lower,
    )
    on_disk_set = set(on_disk)
    listed_set  = {e["file"] for e in existing}

    kept    = [e for e in existing if e["file"] in on_disk_set]
    dropped = [e["file"] for e in existing if e["file"] not in on_disk_set]
    added   = [f for f in on_disk if f not in listed_set]

    for f in added:
        kept.append({
            "file": f,
            "title": "TODO — title",
            "zh":    "TODO — 标题",
            "cap":   "",
            "zhCap": "",
        })

    # ---- 报告 ----
    print("磁盘上图片：%d 张   数组里原有：%d 条" % (len(on_disk), len(existing)))
    if added:
        print("\n新增 %d 张（已追加到数组末尾，标题待填）：" % len(added))
        for f in added:
            print("  + " + f)
    if dropped:
        print("\n移除 %d 条（文件已不存在）：" % len(dropped))
        for f in dropped:
            print("  - " + f)
    if not added and not dropped:
        print("\n已经是同步状态，无需改动。")
        return 0

    if args.dry_run:
        print("\n[--dry-run] 未写入文件。")
        return 0

    HTML.write_text(head + BEGIN + "\n" + render(kept) + "\n" + END + tail,
                    encoding="utf-8")
    print("\n已写入 %s，共 %d 张。" % (HTML.name, len(kept)))
    if added:
        print("下一步：在 photography.html 里搜 TODO，把占位标题改掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
