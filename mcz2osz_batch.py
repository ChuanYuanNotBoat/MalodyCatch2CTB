# -*- coding: utf-8 -*-
"""批量将 Malody Catch 谱面转换为 osu! Catch The Beat 谱面包。

用法：
    python mcz2osz_batch.py                # 直接运行：扫描运行目录下所有 .mcz，转换后归档
    python mcz2osz_batch.py <输入路径> [-o 输出目录] [-r]
      输入路径可以是 .mcz 文件、包含它们的目录（-r 递归）。
      每个成功的谱面包输出一个 .osz（资源拍平至根目录）。

直接运行（无参数）模式：
    - 递归扫描运行目录下的所有 .mcz
    - .osz 输出到运行目录下的 output/ 文件夹
    - 已处理的 .mcz 会移动到 input/ 文件夹归档，防止重复转换
      （若转换完全失败则留在原地，下次运行会重试）

说明：
    - 枚举 mcz 内全部 .mc，仅转换 mode==3 的谱面。
    - 音频优先取 type=1 音效 note 的 sound 字段，否则取歌曲目录中的音频文件。
    - 资源文件按附录 A 规则拍平，并排除 CCE 编辑器私有目录 .mcce-plugin/。
"""

import argparse
import os
import sys
import zipfile

from mc2ctb_core import parse_mc_bytes, remove_stacked_notes, sanitize_filename

AUDIO_EXT = (".ogg", ".mp3", ".wav")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp")
EXCLUDE_DIRS = (".mcce-plugin",)


def find_mc_entries(names):
    """返回 [(arcname, song_dir, filename)]，并排除 .mcce-plugin/。"""
    entries = []
    for name in names:
        parts = name.replace("\\", "/").split("/")
        if parts[-1].lower().endswith(".mc") and not any(
            p in EXCLUDE_DIRS for p in parts[:-1]
        ):
            entries.append((name, "/".join(parts[:-1]), parts[-1]))
    return entries


def list_resource_files(zf, song_dir):
    """返回歌曲目录下的音频和图片资源：{arcname: filename}。"""
    out = {}
    for name in zf.namelist():
        parts = name.replace("\\", "/").split("/")
        if name.endswith("/") or any(p in EXCLUDE_DIRS for p in parts):
            continue
        if song_dir and "/".join(parts[:-1]) != song_dir:
            continue
        if not parts[-1].lower().endswith(AUDIO_EXT + IMAGE_EXT):
            continue
        out[name] = parts[-1]
    return out


def resolve_conflicts(mapping):
    """将 {arcname: flatname} 拍平，并为重名文件追加序号。"""
    used, final = set(), {}
    for arc in sorted(mapping):
        base = sanitize_filename(mapping[arc], "file")
        stem, ext = os.path.splitext(base)
        if base.lower() not in used:
            final[arc] = base
        else:
            k = 1
            while ("%s_%d%s" % (stem, k, ext)).lower() in used:
                k += 1
            final[arc] = "%s_%d%s" % (stem, k, ext)
        used.add(final[arc].lower())
    return final


def convert_mcz(mcz_path, out_dir, dedupe=False):
    """转换单个 mcz。返回 (converted, skipped, errors)。"""
    converted, skipped, errors = 0, 0, []
    with zipfile.ZipFile(mcz_path) as zf:
        mc_entries = find_mc_entries(zf.namelist())
        if not mc_entries:
            return 0, 0, ["未找到 .mc 谱面文件"]

        charts = []
        for arc, song_dir, fname in mc_entries:
            try:
                chart = parse_mc_bytes(zf.read(arc), fname)
                if dedupe:
                    removed = remove_stacked_notes(chart)
                    if removed:
                        print("  [去重] %s: 剔除 %d 个同位多压 Fruit" % (
                            fname, removed))
                charts.append((chart, arc, song_dir, fname))
            except ValueError as e:
                skipped += 1
                print("  [跳过] %s: %s" % (fname, e))
            except Exception as e:
                errors.append("%s: %s" % (fname, e))

        if not charts:
            return 0, skipped, errors

        # 先按歌曲目录收集资源；多数 mcz 只有歌名/0/ 一层目录。
        raw_res = {}
        for sd in sorted({s for _, _, s, _ in charts}):
            raw_res.update(list_resource_files(zf, sd))
        # 兜底：谱面引用但未收集到的文件，在整个 zip 内按文件名查找。
        all_names = {n.replace("\\", "/"): n for n in zf.namelist()}
        needed = set()
        for chart, _, _, _ in charts:
            if chart.audio_filename():
                needed.add(chart.audio_filename())
            if chart.background:
                needed.add(chart.background)
        for fn in needed:
            if fn not in raw_res.values():
                for n, orig in all_names.items():
                    if (n.split("/")[-1] == fn
                            and not any(p in EXCLUDE_DIRS for p in n.split("/"))):
                        raw_res[orig] = fn
                        break
        res_final = resolve_conflicts(raw_res)

        # 生成 .osu 文本。
        osu_texts = []
        for chart, arc, song_dir, fname in charts:
            audio = chart.audio_filename()
            if not audio:
                auds = [f for f in res_final.values()
                        if f.lower().endswith(AUDIO_EXT)]
                audio = auds[0] if auds else None
            if not audio:
                errors.append("%s: 无法确定音频文件" % fname)
                continue
            try:
                text = chart.to_osu_text(audio)
            except Exception as e:
                errors.append("%s: %s" % (fname, e))
                continue
            base = sanitize_filename(
                "%s - %s (%s) [%s]" % (
                    chart.artist or "unknown", chart.title or "unknown",
                    chart.creator or "unknown", chart.version or fname),
                fname.replace(".mc", ""))
            osu_texts.append((base + ".osu", text))
            converted += 1
            print("  [转换] %s (%s) — fruit=%d banana=%d offset=%d AR=%s" % (
                chart.version or fname, fname, chart.count_fruit,
                chart.count_banana_shower, chart.offset, chart.speed - 0.5))

        if not osu_texts:
            return 0, skipped, errors

        # 将 .osu 和资源打包为 .osz。
        out_name = sanitize_filename(
            charts[0][0].title
            or os.path.splitext(os.path.basename(mcz_path))[0],
            "beatmap") + ".osz"
        out_path = os.path.join(out_dir, out_name)
        written = set()
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as oz:
            for fname, text in osu_texts:
                while fname.lower() in written:
                    fname = fname.replace(".osu", "_1.osu")
                written.add(fname.lower())
                oz.writestr(fname, text)
            for arc, final in res_final.items():
                if final.lower() not in written:
                    written.add(final.lower())
                    oz.writestr(final, zf.read(arc))
        print("  [输出] %s" % out_path)
    return converted, skipped, errors


def find_mcz_files(root, recursive):
    if os.path.isfile(root):
        return [root] if root.lower().endswith(".mcz") else []
    files = []
    if recursive:
        for dirpath, _dirnames, filenames in os.walk(root):
            for f in filenames:
                if f.lower().endswith(".mcz"):
                    files.append(os.path.join(dirpath, f))
    else:
        for f in os.listdir(root):
            p = os.path.join(root, f)
            if os.path.isfile(p) and f.lower().endswith(".mcz"):
                files.append(p)
    return sorted(files)


def main():
    ap = argparse.ArgumentParser(
        description="Malody Catch (mcz) → osu! ctb (osz) 批量转换")
    ap.add_argument("input", nargs="?", default=None,
                    help="输入：.mcz 文件或目录（缺省为运行目录，转换后归档到 input 文件夹）")
    ap.add_argument("-o", "--output", default=None,
                    help="输出目录（缺省为运行目录下的 output 文件夹）")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="递归扫描子目录（缺省无参数运行时已默认递归）")
    ap.add_argument("--no-move", action="store_true",
                    help="不把已处理的 mcz 移入 input 文件夹")
    ap.add_argument("--dedupe", action="store_true",
                    help="剔除多压：同一时间且同一 x 的 Fruit 只保留第一个")
    args = ap.parse_args()

    cwd = os.getcwd()
    archive_dir = os.path.join(cwd, "input")
    out_dir = args.output or os.path.join(cwd, "output")
    os.makedirs(out_dir, exist_ok=True)

    if args.input:
        # 显式指定输入：不移动源文件。
        in_root = args.input
        scan_cwd = False
    else:
        # 无参数运行：递归扫描运行目录，并排除 output/input 文件夹。
        in_root = cwd
        scan_cwd = True
        os.makedirs(archive_dir, exist_ok=True)

    mcz_files = find_mcz_files(in_root, args.recursive or scan_cwd)
    if scan_cwd:
        skip = {os.path.abspath(out_dir), os.path.abspath(archive_dir)}
        mcz_files = [p for p in mcz_files
                     if os.path.dirname(os.path.abspath(p)) not in skip]

    if not mcz_files:
        print("未找到 .mcz 文件：%s" % in_root)
        return 1

    total_c = total_s = total_e = 0
    for path in mcz_files:
        print("处理: %s" % path)
        try:
            c, s, errs = convert_mcz(path, out_dir, dedupe=args.dedupe)
        except Exception as e:
            print("  [失败] %s" % e)
            total_e += 1
            continue
        total_c += c
        total_s += s
        for err in errs:
            print("  [错误] %s" % err)
        total_e += len(errs)

        # 转换成功且未指定 --no-move 时移入 input/，防止重复处理。
        if not args.no_move and not args.input and c > 0:
            os.makedirs(archive_dir, exist_ok=True)
            dest = os.path.join(archive_dir, os.path.basename(path))
            k = 1
            base, ext = os.path.splitext(dest)
            while os.path.exists(dest):
                dest = "%s_%d%s" % (base, k, ext)
                k += 1
            try:
                os.replace(path, dest)
                print("  [归档] %s" % dest)
            except OSError as e:
                print("  [警告] 归档失败（%s），下次运行将重复处理" % e)

    print("\n完成：%d 个谱面转换，%d 跳过（非 Catch 等），%d 个错误" % (
        total_c, total_s, total_e))
    return 0


if __name__ == "__main__":
    sys.exit(main())

