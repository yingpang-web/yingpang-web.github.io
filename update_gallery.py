#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_gallery.py — 维护摄影页：同步照片清单 + 生成缩略图。

用法：
    python update_gallery.py                # 同步清单 + 补生成缺失的派生图（日常就用这个）
    python update_gallery.py --dry-run      # 只预览，不写任何文件
    python update_gallery.py --no-thumbs    # 只同步清单，不碰图片
    python update_gallery.py --force-thumbs # 全部重新生成派生图（改了尺寸/质量参数时用）

原图放在别的盘：
    python update_gallery.py --set-source "D:/Photos/homepage"   # 记住位置，只需做一次
    python update_gallery.py                                     # 之后照常跑

    仓库里只保留 thumbs/ 和 large/（网页只用这两个），原图不进 git。
    原图不在时脚本不会删清单 —— 只要派生图还在，条目一律保留。

做两件事：

A. 同步 photography.html 里的 PHOTOS 数组
   1. 已在数组里、文件也还在  -> 原样保留（标题、说明、顺序都不动）
   2. 文件夹里新增的图片      -> 追加到数组末尾，标题填占位符 TODO
   3. 数组里有、文件却没了    -> 从数组里删掉，并在终端提示

B. 生成两级派生图（原图不动）
   photographs/thumbs/  长边 900px   -> 网格用
   photographs/large/   长边 2000px  -> 灯箱用
   自动按 EXIF 旋转摆正、剥掉 EXIF、转 sRGB。已存在且比原图新的会跳过。

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
PHOTO_DIR = ROOT / "photographs"        # 派生图存这里，跟着仓库走
SRC_CFG   = ROOT / ".gallery-source"    # 记住原图在哪（本文件不进 git）


def resolve_source(cli_path):
    """原图目录：命令行 --source > .gallery-source 文件 > 默认 photographs/。"""
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    if SRC_CFG.exists():
        line = SRC_CFG.read_text(encoding="utf-8").strip()
        if line:
            return Path(line).expanduser().resolve()
    return PHOTO_DIR

# 只认这些后缀；photographs/ 下的子文件夹（candidates/ thumbs/ large/）不会被扫描
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# 派生图规格：(子目录名, 长边像素, JPEG 质量)
DERIVATIVES = [
    ("thumbs", 900,  80),   # 网格
    ("large",  2000, 85),   # 灯箱
]

BEGIN = "/* PHOTOS:BEGIN */"
END   = "/* PHOTOS:END */"

# 匹配数组里每一个 { ... } 条目（条目内部没有嵌套花括号，所以这样就够）
OBJ_RE = re.compile(r"\{[^{}]*\}", re.S)


# ---------------------------------------------------------------- 清单同步

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
            "w":     read_field(block, "w"),
            "h":     read_field(block, "h"),
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
        out.append('    zhCap:"%s",' % js_escape(e["zhCap"]))
        out.append('    w:%s, h:%s }%s' % (e.get("w") or 0, e.get("h") or 0, tail))
        if tail:
            out.append("")
    out.append("];")
    return "\n".join(out)


def fill_sizes(entries):
    """把缩略图的实际宽高写进每个条目，供页面做等高分栏和占位。"""
    from PIL import Image
    n = 0
    for e in entries:
        thumb = PHOTO_DIR / "thumbs" / (Path(e["file"]).stem + ".jpg")
        if not thumb.exists():
            continue
        with Image.open(thumb) as im:
            w, h = im.size
        if (str(w), str(h)) != (e.get("w"), e.get("h")):
            n += 1
        e["w"], e["h"] = str(w), str(h)
    return n


def scan_photos(src_dir):
    """原图目录下的图片文件名，按名字排序；不进子目录。"""
    return sorted(
        (p.name for p in src_dir.iterdir()
         if p.is_file() and p.suffix.lower() in EXTS),
        key=str.lower,
    )


def have_derivatives(file_name):
    """这张照片的两级派生图都在吗？原图移走后靠它判断条目是否还有效。"""
    stem = Path(file_name).stem + ".jpg"
    return all((PHOTO_DIR / sub / stem).exists() for sub, _, _ in DERIVATIVES)


# ---------------------------------------------------------------- 派生图

def build_derivatives(files, src_dir, force=False, dry_run=False):
    """为每张原图生成 thumbs/ 和 large/ 两级派生图。返回 (生成数, 跳过数)。"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        print("\n需要 Pillow 才能生成缩略图：pip install Pillow", file=sys.stderr)
        return None

    # 派生图统一叫 <原名去后缀>.jpg，先查一下有没有重名冲突
    stems = {}
    for f in files:
        s = Path(f).stem
        stems.setdefault(s, []).append(f)
    for s, group in stems.items():
        if len(group) > 1:
            print("  ! 同名冲突（派生图会互相覆盖）：%s" % "、".join(group))

    made = skipped = 0
    for sub, edge, quality in DERIVATIVES:
        outdir = PHOTO_DIR / sub
        if not dry_run:
            outdir.mkdir(exist_ok=True)

        for f in files:
            src = src_dir / f
            dst = outdir / (Path(f).stem + ".jpg")

            # 已存在且不比原图旧 -> 跳过
            if (not force and dst.exists()
                    and dst.stat().st_mtime >= src.stat().st_mtime):
                skipped += 1
                continue

            if dry_run:
                made += 1
                continue

            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)      # 按 EXIF 摆正
                im = im.convert("RGB")                # 去 alpha / 统一 sRGB
                im.thumbnail((edge, edge), Image.LANCZOS)
                im.save(dst, "JPEG", quality=quality,
                        optimize=True, progressive=True)   # 不带 exif= 即剥掉元数据
            made += 1

    return made, skipped


def dir_size_mb(path: Path) -> float:
    if not path.is_dir():
        return 0.0
    return sum(p.stat().st_size for p in path.iterdir() if p.is_file()) / 1024 / 1024


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run",      action="store_true", help="只预览，不写文件")
    ap.add_argument("--no-thumbs",    action="store_true", help="跳过派生图生成")
    ap.add_argument("--force-thumbs", action="store_true", help="重新生成全部派生图")
    ap.add_argument("--source", metavar="PATH",
                    help="原图所在目录（默认读 .gallery-source，再默认 photographs/）")
    ap.add_argument("--set-source", metavar="PATH",
                    help="把原图目录记进 .gallery-source，以后不用再输")
    ap.add_argument("--allow-prune", action="store_true",
                    help="确认要从清单里删掉找不到原图的条目")
    args = ap.parse_args()

    if args.set_source:
        p = Path(args.set_source).expanduser().resolve()
        if not p.is_dir():
            print("目录不存在：%s" % p, file=sys.stderr)
            return 1
        SRC_CFG.write_text(str(p), encoding="utf-8")
        print("原图目录已记住：%s" % p)
        print("（记在 %s，该文件已被 .gitignore 排除）" % SRC_CFG.name)

    if not HTML.exists():
        print("找不到 %s" % HTML, file=sys.stderr)
        return 1
    if not PHOTO_DIR.is_dir():
        print("找不到 %s" % PHOTO_DIR, file=sys.stderr)
        return 1

    src_dir = resolve_source(args.source or args.set_source)
    if not src_dir.is_dir():
        print("原图目录不存在：%s" % src_dir, file=sys.stderr)
        print("用 --source PATH 指定，或 --set-source PATH 记住它。", file=sys.stderr)
        return 1
    if src_dir != PHOTO_DIR:
        print("原图目录：%s" % src_dir)

    html = HTML.read_text(encoding="utf-8")
    if BEGIN not in html or END not in html:
        print("photography.html 里找不到 PHOTOS:BEGIN / PHOTOS:END 标记。", file=sys.stderr)
        return 1

    head, rest = html.split(BEGIN, 1)
    body, tail = rest.split(END, 1)

    existing    = parse_existing(body)
    on_disk     = scan_photos(src_dir)
    on_disk_set = set(on_disk)
    listed_set  = {e["file"] for e in existing}

    added   = [f for f in on_disk if f not in listed_set]
    missing = [e["file"] for e in existing if e["file"] not in on_disk_set]

    # 找不到原图的条目分两种：派生图还在（照片仍能正常显示，只是原图搬走了）
    # 和派生图也没了（真的该删）。前者绝不能动。
    orphan_ok  = [f for f in missing if have_derivatives(f)]
    orphan_bad = [f for f in missing if not have_derivatives(f)]

    prune = orphan_bad if args.allow_prune else []
    kept  = [e for e in existing if e["file"] not in set(prune)]

    for f in added:
        kept.append({
            "file": f,
            "title": "TODO — title",
            "zh":    "TODO — 标题",
            "cap":   "",
            "zhCap": "",
        })

    # ---- A. 清单 ----
    print("原图目录：%d 张   清单里：%d 条" % (len(on_disk), len(existing)))
    if added:
        print("\n新增 %d 张（已追加到清单末尾，标题待填）：" % len(added))
        for f in added:
            print("  + " + f)
    if orphan_ok:
        print("\n%d 条找不到原图，但 thumbs/ 和 large/ 都在 —— 保留，网页照常显示。" % len(orphan_ok))
        print("（原图搬走了就是这个情况，属正常）")
    if orphan_bad:
        print("\n%d 条既没有原图、派生图也不全：" % len(orphan_bad))
        for f in orphan_bad:
            print("  ? " + f)
        if args.allow_prune:
            print("  已按 --allow-prune 从清单删除。")
        else:
            print("  暂未删除。确认要删请加 --allow-prune；")
            print("  如果只是原图挪了位置，请改用 --source 指向正确的目录。")
    if not added and not missing:
        print("清单已是同步状态。")

    # ---- B. 派生图 ----（先生成，下面记录尺寸要用到 thumbs/）
    if not args.no_thumbs:
        print("\n生成派生图 …")
        res = build_derivatives(on_disk, src_dir, force=args.force_thumbs,
                                dry_run=args.dry_run)
        if res is None:
            return 1
        made, skipped = res
        print("  生成 %d 个，跳过 %d 个（已是最新）" % (made, skipped))
        if not args.dry_run:
            print("  仓库里 thumbs/ %.1f MB + large/ %.1f MB = %.1f MB" % (
                dir_size_mb(PHOTO_DIR / "thumbs"),
                dir_size_mb(PHOTO_DIR / "large"),
                dir_size_mb(PHOTO_DIR / "thumbs") + dir_size_mb(PHOTO_DIR / "large"),
            ))
            if on_disk:
                print("  原图 %.1f MB（不进仓库）" % (
                    sum((src_dir / f).stat().st_size for f in on_disk) / 1024 / 1024))

    # ---- C. 记录尺寸 ----（页面靠它做等高分栏 + 占位，避免图片加载时页面跳动）
    changed = 0
    if not args.dry_run:
        try:
            changed = fill_sizes(kept)
            if changed:
                print("\n更新了 %d 条的尺寸记录。" % changed)
        except ImportError:
            print("\n没装 Pillow，跳过尺寸记录。", file=sys.stderr)

    if (added or prune or changed) and not args.dry_run:
        HTML.write_text(head + BEGIN + "\n" + render(kept) + "\n" + END + tail,
                        encoding="utf-8")
        print("已写入 %s，共 %d 张。" % (HTML.name, len(kept)))

    if args.dry_run:
        print("\n[--dry-run] 未写入任何文件。")
    elif added:
        print("\n下一步：在 photography.html 里搜 TODO，把占位标题改掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
