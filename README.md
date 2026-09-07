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

常用选项：

- `-r`：递归扫描指定目录。
- `-o`：指定输出目录。
- `--no-move`：不将成功处理的文件移动到 `input/`。
- `--dedupe`：合并同一时间且同一位置的 Fruit。

## 转换规则

- 只转换 `mode == 3` 的 Catch 谱面。
- `Rain` 转换为 `BananaShower`，普通 note 转换为 `Fruit`。
- 保留多押和重叠 note；仅在使用 `--dedupe` 时执行去重。
- 音频优先使用音效 note 的 `sound` 字段，否则从歌曲目录中查找音频资源。
- 资源文件会拍平到 `.osz` 根目录，重名文件自动追加序号。

## 文件结构

- `mc2ctb_core.py`：时间线、谱面解析和 `.osu` 文本生成。
- `mcz2osz_batch.py`：`.mcz` 扫描、资源处理和 `.osz` 打包。

## 许可证

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE)。
