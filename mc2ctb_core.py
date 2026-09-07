# -*- coding: utf-8 -*-
"""Malody Catch（.mc）到 osu! Catch The Beat（.osu）的核心转换逻辑。

格式基准：
  - Malody mc 格式：CCE《谱面格式规范.md》/《ARCHITECTURE_FORMAT_REFERENCE.md》
  - osu ctb 输出：rmstZ 转换器 (toOsuText "ctb") + osu lazer LegacyBeatmapDecoder
时间基准：
  - Malody 侧：t(beat) = 拍段累计 − meta.offset（CCE MathUtils::buildBpmTimeCache，
    accumulated 初始为 −offsetMs）。meta.offset 缺失时回退读取第一条
    type=1 音效 note 的 offset（CCE ChartIO.cpp 行为）。
  - osu 侧：时间均为相对音频的绝对毫秒，无独立 offset 字段；
    因此首条红线 TimingPoint 的 time = −offset，所有物量时间同样减 offset，
    两侧锚系一致。
转换规则（用户定稿）：
    - CircleSize 为 3.8。
    - ApproachRate 为 meta.mode_ext.speed - 0.5；speed 缺省为 5（AR 4.5）。
    - Source 为 "Malody catch"。
    - 不剔除多押或重叠 note，按 1:1 保留。
    - note 落在整数拍（分子为 0）时附加 NewCombo（+4）。
    - Rain（type=3，无 x）转换为 BananaShower（type 8）；普通 note 转换为 Fruit（type 1）。
"""

import json
import re

CIRCLE_SIZE = 3.8
DEFAULT_SPEED = 5          # mode_ext.speed 缺省值
FILE_FORMAT_VERSION = 14   # osu file format v14
SOURCE_TAG = "Malody catch"


def _beat_to_float(beat):
    """将 [b, n, d] 转换为 b + n / d。"""
    b, n, d = beat
    d = d if d else 1
    return float(b) + float(n) / float(d)


class BpmTimeline:
    """CCE MathUtils::buildBpmTimeCache 和 beatToMs 的 Python 等价实现。"""

    def __init__(self, time_list, offset_ms=0):
        # time_list 是 mc 的 time 数组：[{"beat": [b, n, d], "bpm": 145.0}, ...]
        entries = sorted(
            ((_beat_to_float(t["beat"]), float(t["bpm"])) for t in time_list),
            key=lambda e: e[0],
        )
        if not entries:
            entries = [(0.0, 120.0)]
        if entries[0][0] > 0:  # 确保时间线从 beat 0 开始。
            entries.insert(0, (0.0, entries[0][1]))
        self.offset = float(offset_ms)
        self.segments = []  # (start_beat, start_ms, bpm)
        acc = -self.offset  # CCE 中 accumulated 的初始值为 -offset。
        for i, (beat, bpm) in enumerate(entries):
            if bpm <= 0:
                bpm = 120.0
            if i > 0:
                pbeat = entries[i - 1][0]
                pbpm = self.segments[-1][2]
                acc += (beat - pbeat) * (60000.0 / pbpm)
            self.segments.append((beat, acc, bpm))

    def beat_to_ms(self, beat_val):
        segs = self.segments
        for i in range(len(segs)):
            b0, m0, bpm = segs[i]
            b1 = segs[i + 1][0] if i + 1 < len(segs) else float("inf")
            if b0 <= beat_val < b1 or i == len(segs) - 1:
                return m0 + (beat_val - b0) * (60000.0 / bpm)
        return None

    def timing_points(self):
        """返回 [(time_ms, ms_per_beat, is_first), ...]，每个 BPM 段一条红线。"""
        return [
            (m0, 60000.0 / bpm, i == 0)
            for i, (b0, m0, bpm) in enumerate(self.segments)
        ]


class McChart:
    """一个 mc 谱面（mode==3 Catch）。"""

    def __init__(self, data, source_name=""):
        if not isinstance(data, dict):
            raise ValueError("mc 根节点不是 JSON 对象")
        meta = data.get("meta") or {}
        self.source_name = source_name
        self.mode = meta.get("mode")
        if self.mode != 3:
            raise ValueError("非 Catch 谱面 (mode=%r)" % (self.mode,))

        self.title = meta.get("song", {}).get("title", "") or ""
        self.artist = meta.get("song", {}).get("artist", "") or ""
        self.creator = meta.get("creator", "") or ""
        self.version = meta.get("version", "") or ""
        self.background = meta.get("background", "") or ""
        self.speed = (meta.get("mode_ext") or {}).get("speed", DEFAULT_SPEED)

        # offset 优先读取 meta.offset，否则回退到第一条 type=1 音效 note。
        offset = meta.get("offset")
        if offset is None:
            for n in data.get("note", []):
                if n.get("type") == 1 and n.get("offset") is not None:
                    offset = n["offset"]
                    break
        self.offset = int(offset or 0)

        self.timeline = BpmTimeline(data.get("time") or [], self.offset)

        # 音效 note（type=1）不生成物量，只记录音频文件名和时间。
        self.sounds = []
        self.notes = []       # 转换后的 hit objects
        self.count_fruit = 0
        self.count_banana_shower = 0
        self.skipped = 0

        for n in data.get("note", []):
            ntype = n.get("type", 0)
            if ntype == 1:
                self.sounds.append({
                    "sound": n.get("sound", ""),
                    "vol": n.get("vol", 100),
                    "offset": n.get("offset", 0),
                    "time_ms": self.timeline.beat_to_ms(_beat_to_float(n.get("beat", [0, 0, 1]))),
                })
                continue
            if ntype == 3:  # Rain 转换为 BananaShower。
                start = _beat_to_float(n["beat"])
                end = _beat_to_float(n.get("endbeat") or n["beat"])
                t0 = self.timeline.beat_to_ms(start)
                t1 = self.timeline.beat_to_ms(end)
                if t1 <= t0:
                    self.skipped += 1
                    continue
                combo = 4 if self._is_whole_beat(start) else 0
                self.notes.append(
                    {"type": 8 | combo, "x": 256, "y": 0, "time": t0, "end": t1})
                self.count_banana_shower += 1
                continue
            # 普通音符（type=0 或缺失）。
            if "x" not in n:
                self.skipped += 1
                continue
            beat_val = _beat_to_float(n["beat"])
            t0 = self.timeline.beat_to_ms(beat_val)
            x = int(round(max(0, min(512, int(n["x"])))))
            combo = 4 if self._is_whole_beat(beat_val) else 0
            self.notes.append(
                {"type": 1 | combo, "x": x, "y": 0, "time": t0, "end": None})
            self.count_fruit += 1

        self.notes.sort(key=lambda o: (o["time"], o["end"] if o["end"] is not None else o["time"]))

    @staticmethod
    def _is_whole_beat(beat_val):
        # 整数拍：分数部分为 0，容许浮点误差 1e-6。
        return abs(beat_val - round(beat_val)) < 1e-6

    def audio_filename(self):
        """音频文件：优先音效 note 的 sound，否则由调用方兜底。"""
        for s in self.sounds:
            if s["sound"]:
                return s["sound"]
        return None

    # ---------- osu 输出 ----------

    def _metadata_section(self):
        title = self.title or "unknown"
        artist = self.artist or "unknown"
        version = self.version or "Catch"
        tags = " ".join(t for t in ["malody", "catch", self.source_name] if t)
        return (
            "[Metadata]\r\n"
            "Title:%s\r\n"
            "TitleUnicode:%s\r\n"
            "Artist:%s\r\n"
            "ArtistUnicode:%s\r\n"
            "Creator:%s\r\n"
            "Version:%s\r\n"
            "Source:%s\r\n"
            "Tags:%s\r\n"
            "BeatmapID:0\r\n"
            "BeatmapSetID:-1\r\n" % (
                title, title, artist, artist, self.creator or "unknown",
                version, SOURCE_TAG, tags)
        )

    def to_osu_text(self, audio_filename):
        """生成 .osu 文件文本。audio_filename 必填。"""
        if not audio_filename:
            raise ValueError("无法确定音频文件名")
        ar = self.speed - 0.5
        min_time = min((o["time"] for o in self.notes), default=0)
        audio_lead_in = max(0, int(-min_time + 500)) if min_time < 0 else 0

        lines = [
            "osu file format v%d" % FILE_FORMAT_VERSION,
            "",
            "[General]",
            "AudioFilename: %s" % audio_filename,
            "AudioLeadIn: %d" % audio_lead_in,
            "PreviewTime: -1",
            "Countdown: 0",
            "SampleSet: Normal",
            "StackLeniency: 0.7",
            "Mode: 2",
            "LetterboxInBreaks: 0",
            "",
            "[Editor]",
            "DistanceSpacing: 1",
            "BeatDivisor: 4",
            "GridSize: 8",
            "",
        ]
        lines += self._metadata_section().split("\r\n")
        lines += [
            "",
            "[Difficulty]",
            "HPDrainRate:5",
            "CircleSize:%s" % CIRCLE_SIZE,
            "OverallDifficulty:5",
            "ApproachRate:%s" % ar,
            "SliderMultiplier:1.8",
            "SliderTickRate:1",
            "",
            "[Events]",
            "//Background and Video events",
        ]
        if self.background:
            lines.append("0,0,%s,0,0" % self.background)
        lines += ["//Break Periods", "//Storyboard Layer 0 (Background)",
                  "//Storyboard Layer 1 (Fail)", "//Storyboard Layer 2 (Pass)",
                  "//Storyboard Layer 3 (Foreground)",
                  "//Storyboard Sound Samples", "", "[TimingPoints]"]
        for time_ms, ms_per_beat, _first in self.timeline.timing_points():
            # 红线字段：time, beatLength, meter, sampleSet, sampleIndex,
            # volume, uninherited, effects。
            lines.append("%.0f,%.6f,4,2,0,100,1,0" % (time_ms, ms_per_beat))
        lines += ["", "[HitObjects]"]
        for o in self.notes:
            # osu 字段：x, y, time, type, hitSound；combo 在 type 位内。
            if o["type"] & 8:  # BananaShower 使用 spinner 位。
                lines.append("%d,%d,%d,%d,0,%d" % (
                    o["x"], o["y"], int(round(o["time"])), o["type"],
                    int(round(o["end"]))))
            else:  # Fruit 使用 circle 类型。
                lines.append("%d,%d,%d,%d,0" % (
                    o["x"], o["y"], int(round(o["time"])), o["type"]))
        return "\r\n".join(lines) + "\r\n"


def remove_stacked_notes(mc, tolerance_ms=0):
    """剔除多压：同一时间（同一毫秒值）的一组同押 Fruit 合并为一个，
    x 取该组所有 note 的中间位置（平均值）。

    返回被剔除的数量。注意：BananaShower 不参与剔除。
    """
    kept, removed = [], 0
    stack = []  # 当前同押 Fruit 组。
    for o in mc.notes:  # notes 已按时间排序。
        if (o["type"] & 8) or not (o["type"] & 1):
            kept.append(o)
            continue
        t = o["time"]
        if not stack:
            stack.append(o)
        elif t - stack[0]["time"] == tolerance_ms or (tolerance_ms and t - stack[0]["time"] <= tolerance_ms):
            stack.append(o)
        else:
            if len(stack) > 1:
                removed += len(stack) - 1
                mid = stack[len(stack) // 2]  # 取组内中间位置的 note。
                stack[0]["x"] = mid["x"]
            kept.append(stack[0])
            stack = [o]
    if len(stack) > 1:
        removed += len(stack) - 1
        mid = stack[len(stack) // 2]
        stack[0]["x"] = mid["x"]
    kept.append(stack[0])
    if removed:
        mc.notes = kept
        mc.count_fruit -= removed
    return removed


def parse_mc_bytes(raw, source_name=""):
    """从字节流解析 mc，自动处理 UTF-8-BOM、UTF-8 和 GBK。"""
    if isinstance(raw, bytes):
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                raw = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("无法解码 mc 文件")
    return McChart(json.loads(raw), source_name)


def sanitize_filename(name, default="output"):
    """返回适用于 osu 和文件系统的安全文件名。"""
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or default

