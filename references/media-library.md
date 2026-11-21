# 见秋个人素材库

工作区的 media-library/index.html 可离线浏览、播放和筛选。catalog.json 是可移走的总目录；items/ 含真实媒体、预览及附件。技能包带 CLI 和页面模板。搬库时一并复制 catalog.json、items/、index.html 和已生成的 visual-reviews/、learning-results/。大型实拍和源录音保存在个人素材库。脚本依次读取 --root、CREATOR_MEDIA_LIBRARY、当前工程逐级向上的 media-library、本机配置的个人库。前三者优先于个人默认。尚未配置且无本地库时，新建登记会在当前目录建立 media-library。依赖 Python 3.8+、ffprobe、ffmpeg。

视觉风格、动作白模、用光卡、组合预览及审片回写见[视觉图鉴](visual-atlas.md)。

**开片先查素材和内容。** 需要记录本片的实际选择时，以 `project_learning.py --project 本期 find 词` 为入口，检索正文、卡片审片笔记和跨片使用效果。运行与回写见[自主整理](continuous-learning.md#让下一期搜到使用效果)。 用 `search --scope all` 同时找可制作、限报道和仅供研习的条目，再按实际用途选用。可输入名称、多个短词，或“机房里有人在查线”这样的用途描述。检索先找包含全部词的精确结果；没有命中时，再按主体、动作的同义短词回退。各个主体和动作仍须同时匹配，未识别的具体词也会保留。结果优先展示标题、标签、别名和用途更相关的卡片，回退结果最多 8 条。类别、使用范围和视觉类型筛选同时生效。检索涵盖用途、作者、来源、附件名和登记的文字说明。查讲稿、术语或研究结论时加 `--content`，还会检索当前入库的 Markdown、纯文本和字幕正文。PDF、图片和视频可按“内容摘要”与来源定位，再打开原件复核。若工作区已有项目讲稿或研修索引，可按相关主题补查现有正文。检索结果的 preview 可打开试听或试看。卡里已有动作、方向、视线目标、源入出点或剪点时，source_notes 会保留原来的单位和表述。短预览可能截过头尾，定剪点时回到原件核对同一动作，再决定是否需要外找或补拍。

CLI、agent 的 find 和离线页共用 scripts/library-search.json 的同义词与排序规则。已有卡片的标签和别名会自动参与拆词，新主体可以随入库直接被找到。补同义说法时将能互换的主体或动作写入同一 concepts 条目的 terms，保持具体对象和动作的含义一致。某条素材独有的口语说法写到该卡 aliases；先复看原件确认。修改规则后执行 library.py browse 更新离线页，再以一条应命中、一条应排除的描述验证。

“同一段视频压两遍”和“把同一段视频压两次”都可找到压缩工程。“就想让前景慢慢虚掉”可找到前景到背景的拉焦卡。口语回退时，左右进出方向和板上焊接这类动作从标题、标签、别名或视觉词中取证。检索全文仍可复查文稿里提到的术语。开发板旁焊细线与焊球微距各有用途，找板上焊接近景时需另找相符的画面。

同一镜改过说法才找到素材时，用[跨期检索中的选卡记录](continuous-learning.md#让下一期搜到使用效果)保存实看确认过的原句。网页、命令行和下一个工程均能使用该别名。同一素材版本重新登记时保留学到的说法；换了素材版本后重新确认。

每一项保留标题、作者、确切来源、许可、用途、标签、带校验值的文件和预览。视频有以秒计的原片入出点；拟音有接触点；乐段有循环和自然收尾；模板列出可替换输入。拍摄日期、书页或原始时间码、来源条款页等可存放在 editorial 里。转场条目可同时提供前镜、后镜、成品短样、切点和可替换的工程。素材来源和使用条件随项目保存在库清单或发布说明。新闻画面中的制作备注按正片文字规范处理。

在一台新机器上先用 `python3 "$SK/scripts/library.py" set-default /自己的素材库` 指定已搬好的目录。它只更新本机 ~/.config/creator-video/library.json（支持 XDG_CONFIG_HOME）。以后不在 video-shop 里的项目也能直接搜索和提取。以下 SK 指向已安装的 creator-video 目录。

```sh
python3 "$SK/scripts/library.py" search 翻页
python3 "$SK/scripts/library.py" search --scope all "Item 5.02"
python3 "$SK/scripts/library.py" search --scope all --content 光郡
python3 "$SK/scripts/library.py" search --scope all 翻页
python3 "$SK/scripts/library.py" search --category ambience --scope production
python3 "$SK/scripts/library.py" search --category continuity --json
python3 "$SK/scripts/library.py" use broll-paper-turn --project videos/本期
python3 "$SK/scripts/library.py" use evidence-nvda-filing --purpose editorial --project videos/本期
python3 "$SK/scripts/library.py" search --scope reference 镜头
```

项目会收到 assets/library/资产ID/ 里原名的主文件、分轨或工程附件，以及项目根目录的 library-uses.json。剪辑直接读取这些文件。每条使用记录包含素材版本、作用范围、原始权利与入出点。默认检索面向制作与符合条件的报道；研究须显式查询 reference，CLI 拒绝将参考片提取到正片。对报道专用材料阅读原条款后使用 --purpose editorial。

**入库。** 在新素材旁写一个 JSON 对象或列表，file、preview、companions[*].file 均相对于这份登记文件。记录的源文件和修改版可分别作为条目，或将原始录音/原稿标成具名附件。命名简短、稳定的 ASCII id。 可选 aliases 存用户检索时会说的同义动作名称，例如手翻纸、翻纸和翻页。补充这些查找词不会改变媒体版本。例子见库内 recipes，以及下例。

```json
{"id":"desk-switch-01","title":"工作台机械开关按下","category":"foley","kind":"audio","file":"switch.wav","tags":["按下","工作台"],"use":"镜头中开关接触时对齐第一声","author":"见秋","source":"本期工作台实录","license":"见秋自录自用","scope":"production","editorial":{"in":0.1,"out":1.0,"cue":0.32}}
```

```sh
python3 "$SK/scripts/library.py" --root /素材库路径 add /新录音/switch-entry.json
python3 "$SK/scripts/library.py" --root /素材库路径 verify
python3 "$SK/scripts/library.py" --root /素材库路径 browse
```

主文件 kind 可选 video、audio、image、document、template、data；文档、模板和数据需提供真实预览。视频/音频未附预览时，CLI 从可用入点产生浏览器可播放的样片。loop、tail 接受可用起止秒对；额外附件为 `[{"label":"音乐分轨","file":"stem.wav"}]`。新闻来源短摘、版权受限人物档案用 editorial；普通获准制作的素材用 production；仅供观看学习的素材用 reference。私用不等于公开发布权，保留原许可的署名和限制。

同一 id、内容和元数据再次登记会复用原版本。修订条目时显式 --replace，旧版本仍在 items/，既有项目持有独立副本。新增、版本发布及同一项目多次提取加锁；未完成校验的版本不会成为目录当前项。对修改过的项目副本执行 use 会报告冲突。verify 检查所有当前主件、附件和预览的 SHA-256。当前网页可用 browse 从总目录再生。无库存的分类在浏览器内显示“待入库”。

## 开源 SVG 图标

视频中要标出设备、接口、操作或状态时，先用相应中文名查 illustration。已有音频状态、设备网络、硬件与实验仪器图标。缺项可从 [Lucide](https://lucide.dev/icons/) 查线条图标；需要另一种风格时可查 [Tabler](https://tabler.io/icons) 或 [Fluent System Icons](https://github.com/microsoft/fluentui-system-icons)。下载前核对项目原仓库的许可与该文件来源。Lucide 的 ISC 与 Feather 派生图标的 MIT 声明随原件保留；Tabler、Fluent 按其项目许可处理。

下载本镜需要且库内缺少的 SVG，保留固定版本或提交、作者、原件 URL、完整许可、哈希及中文检索词。原始 SVG 入库，改色、改描边或动画版作为附件，导出 PNG 时保留透明底。沿用本期既有的描边粗细、圆角、颜色和视觉尺寸，在实际视频尺寸下看细线是否清楚。符号用于帮助定位部件或解释关系，实物型号、引脚布局和测量值回到原件核对。用到新的图标及有效搭配时随片回写素材卡。

| 必备类别 | id | 专题扩展 | id |
| --- | --- | --- | --- |
| 实拍与录屏 | capture | 纹理、叠加与背景 | texture |
| 图片、档案与证据 | evidence | 风格化转场与特效 | effects |
| 解释动效与图表 | explain | 图标、插画、地图与 3D | illustration |
| 动作拟音与提示音 | foley | 专题实拍与空镜 | broll |
| 环境声 | ambience | LUT 与调色 | grade |
| BGM 与可剪乐段 | music | 音乐短标与进阶声音 | stings |
| 基础转场与衔接 | continuity | 完整段落与工程范例 | sequence |
| 品牌与平面包装 | brand | 优秀作品研究 | reference |
| 旁白与发音 | voice | | |
