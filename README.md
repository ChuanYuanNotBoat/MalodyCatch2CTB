# Malody Catch to osu! Catch The Beat

将 Malody Catch（`.mc` / `.mcz`）谱面转换为 osu! Catch The Beat（`.osu` / `.osz`）。

## 环境

- Python 3.8 或更高版本
- 仅使用 Python 标准库，无需安装第三方依赖

## 使用

直接运行会递归扫描当前目录下的 `.mcz` 文件，将结果写入 `output/`，成功处理的源文件归档到 `input/`：

```powershell
python .\mcz2osz_batch.py
```

也可以指定单个文件或目录：

```powershell
python .\mcz2osz_batch.py path\to\chart.mcz
python .\mcz2osz_batch.py path\to\charts -r -o path\to\output
```

交互运行时，整批转换前询问一次判定；回车采用 **E 判**，也可输入 A/B/C/D/E 或目标 CS（0–10）。非交互运行默认使用 E 判，不等待输入。

也可直接通过参数指定，跳过询问：

```powershell
python .\mcz2osz_batch.py path\to\chart.mcz --judge E
python .\mcz2osz_batch.py path\to\charts -r --judge C --pro
python .\mcz2osz_batch.py path\to\chart.mcz --cs 6.25
```

常用选项：

- `-r`：递归扫描指定目录。
- `-o`：指定输出目录。
- `--no-move`：不将成功处理的文件移动到 `input/`。
- `--dedupe`：合并同一时间且同一位置的 Fruit。
- `--judge A/B/C/D/E`：按 Malody 判定换算 CS。
- `--pro`：判定换算时加入 Pro 的 0.95 倍盘宽；不能与 `--cs` 同用。
- `--cs 数值`：直接指定 CS（0–10）；不能与 `--judge` 同用。

## 转换规则

- CS 按实际横向接取宽度等效换算，默认 E 判约 **CS 5.398126464**，不再固定为 3.8。A/B/C/D/E 分别约为 **2.1061 / 3.3105 / 4.1134 / 4.7558 / 5.3981**；Pro 分别约为 **2.6079 / 3.7521 / 4.5149 / 5.1251 / 5.7354**。
- 换算依据本地 Malody V `WidgetCatchField.JudgeScaler`：`W = 96 × 判定倍率 × Pro倍率`，以及 osu! `Catcher.CalculateCatchWidth`：`W = 106.75 × 0.8 × [1 − 0.14 × (CS − 5)]`，场地宽度统一为 512。使用连续判定宽度，不加入 Malody Auto 的减宽、osu 历史 fudge 或星级计算修正。红果生成与纵向判定机制仍有差异，CS 只匹配横向盘宽。
- 只转换 `mode == 3` 的 Catch 谱面。
- `Rain` 转换为 `BananaShower`，普通 note 转换为 `Fruit`。
- 保留多押和重叠 note；仅在使用 `--dedupe` 时执行去重。
- 音频优先使用音效 note 的 `sound` 字段，否则从歌曲目录中查找音频资源。
- 资源文件会拍平到 `.osz` 根目录，重名文件自动追加序号。

## 文件结构

- `mc2ctb_core.py`：时间线、谱面解析和 `.osu` 文本生成。
- `mcz2osz_batch.py`：`.mcz` 扫描、资源处理和 `.osz` 打包。
- `test_conversion.py`：判定换算、交互选择和整包转换回归；使用 `python -m unittest -v test_conversion` 运行。

## 许可证

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE)。
