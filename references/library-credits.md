# 本片实际使用素材的自动署名

正式 `export.py` 会读取当前 `edit.json`、准备后的 `timeline.json` 和工程的 `library-uses.json`，仅为实际出现在画面或声音中的库素材生成署名。只领用、已删镜头、静音的音乐或音效不进入署名。领用仍用 `library.py use`，不用另维护一份素材清单。

导出候选的 `credits/library-auto/` 包含：

- `publish.txt`：可复制到发布说明的合并署名，含作者、原作、许可、必要声明和本片改动。
- `sources.md`：逐项说明和原始署名/许可文档链接。
- `usage.json`：对应镜头、输入范围、匹配方式、库版本和文件 SHA256。
- 每项素材的原始署名、许可与来源文档副本。

候选冻结、审片指纹和最终签发已包含这些文件，修改冻结后的署名会使原指纹失效。人工撰写的工程 `sources.md`、`credits.md` 和 `edit.delivery_credits` 保持独立，仍按原机制冻结。自动目录是保留命名空间，不要手动覆盖。

## 原片、合成图与声音怎么匹配

直接使用领用文件时，通过原路径和 SHA256 匹配；复制到别处但内容完全相同时，通过唯一归属的 SHA256 匹配。内容改过的文件、合成镜头、图标覆盖、混音和渲染后的段落需要明确映射。相同内容属于多个库条目时不会猜测归属；在映射中指定确切条目。

在 `edit.json` 加 `library_credits`，每个贡献关系一项：

```json
{
  "library_credits": [
    {
      "file": "shots/detail.mp4",
      "asset": "broll-board-side-solder-contact",
      "source_files": ["original.mp4"],
      "changes": "截取原片9–15秒，统一1080p/25fps，编码后裁切同位置细节并排比较"
    },
    {
      "file": "effects.wav",
      "asset": "ui-kenney-confirm",
      "source_files": ["confirmation_001.ogg"],
      "range": [8.2, 8.8],
      "changes": "在8.2秒叠加提示音，调整响度"
    }
  ]
}
```

`file` 必须是本次实际渲染的 `shot.visual`、旁白、配音片段、`music` 或 `effects` 输入。`source_files` 必须精确指向该次领用记录中的文件名或完整相对路径；不能把同名文件或未经记录的文件冒充库原件。`changes` 写具体改动，源片截取区间也写在这里。

`range` 可选，单位为秒，表示贡献在 **派生输入文件** 中的位置，左闭右开；它与本次实际使用区间相交时才署名。不是源片的截取范围。不填表示该素材贡献覆盖整个输入。同一合成镜头可映射多个条目，同一条目也可映射多个镜头。直接剪用库内成片时，按附件自己的时间码和实际输出的画面或声音筛选作者。比如三镜打印机示范只取中间的原创路径图时，发布文字会列路径图的作者。若在派生文件里再次剪用某个成片附件，可以加 `"source_ranges": {"printer-demo.mp4": [3.0, 6.0]}` 指定所用原附件区间。完全删除的镜头会从署名移除。已有 `visual_cards` 的有效切段选择也参与署名，使用记录中的改编说明。

改完映射后重新 `prepare.py`，再正式导出。手工执行 `library_credits.py --project 工程目录` 可只读检查匹配结果；`unmatched_inputs` 表示尚未识别的输入，不等于必有版权问题。外部来源仍写人工来源文档。封面专用且未进入正片的素材，也保留在人工来源中。

### 已登记的混音轨改名时

已经暂存过候选的工程会在新导出时对照上一轮冻结的 `usage.json`。混音轨改名且字节一致时，旧映射按摘要恢复。也可预先给 `library_credits` 的派生输入填入 `input_sha256`，在第一次暂存前识别改名。重新渲染导致字节变化时请核对实际来源并更新映射和已填的摘要。有无法确认归属的替换音轨时，导出会在控制台、`sources.md` 和 `usage.json` 提示复核；真正移除或静音的音轨不进入发布文字。

## 原文署名从哪里来

多来源工程卡可在入口的 `editorial.credit_sources` 中为每个作者或权利主体写好完整的发布文字，在 `editorial.file_credits` 指定每个附件使用了哪些来源。下例展示一张三件附件的卡；每个附件都要有对应条目，登记时会检查文件名和来源。旧卡仍沿用其原始卡片署名。旧工程保留领取时的版本，新声明随新版本领取。

```json
{
  "credit_sources": {
    "diagram": "路径图由见秋绘制，供见秋自有视频使用。",
    "printer": "《3D Printing at FIMS Library》，摄影 Gogerr。原作 https://commons.wikimedia.org/wiki/File:3D_Printing_at_FIMS_Library.webm 。CC BY 4.0 https://creativecommons.org/licenses/by/4.0/ 。"
  },
  "file_credits": {
    "printer-path.png": ["diagram"],
    "printer-source.webm": ["printer"],
    "short.mp4": [
      {"source": "printer", "stream": "picture", "range": [0, 2]},
      {"source": "diagram", "stream": "picture", "range": [2, 4]}
    ]
  }
}
```

`stream` 可选填 `picture` 或 `audio`。相机拍摄、配乐和提示音都已混入成片时，可让新的剪辑按其选用的画面或音轨分别带回署名。完整的第三方发布声明、必要声明和许可链接应写在对应来源文字里。共享许可档随素材冻结供核查，发布文字只取命中的附件贡献。

优先使用条目 `editorial.publish_credit` 的明确文本，或附带 Markdown 中以下专用区域：

```markdown
<!-- publish-credit:start -->
此处仅放应公开发布的署名原文。
<!-- publish-credit:end -->
```

也识别标题恰为 `## 发布署名`、`## 署名文本` 或 `## Attribution text` 的小节。附带 JSON 的 `required_attribution`、`attribution_text`、`non_endorsement_disclaimer` 字符串按原文追加，`license_page` 或 `license_url` 追加许可链接。旧 DVIDS 卡的 `## 可复制到片尾或发布说明的来源` 小节只提取引用块中的必要声明，避免把旧选段时间、内部登记说明或完整脚本混入发布文字。

没有专门署名原文时，使用已领用版本的作者、原作链接和许可元数据，并追加本片实际声明的改动。全部原始许可文档仍随交付保留。新增明确署名文字后，应重新登记条目并重新领用该版本，不能悄悄修改工程中已经校验的许可原文。

## 重导出和签发

新导出会按当前时间线重新计算，删掉镜头或音效后相应署名和必要声明会消失。对同一已冻结视频重新暂存或复签，则保留该视频原来冻结的自动署名，不拿后来改动的活工程替换它。最终交付路径为 `out/credits/library-auto/publish.txt`，与其他来源文档一起进入最终交付清单。
