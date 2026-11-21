# 用视觉卡把参考变成见秋自己的镜头

遇到需要设计的镜头，先在分镜写明观众需要看见的变化。可以用口语搜动作，如“把右边的原件放大占满全屏”，也可以搜专业名称。看白模确认运动，再看实装判断文字大小、主体位置和阅读时间。实拍本身已经交代清楚时可以一直用原镜头。

## 做一张可用的卡

在已有 [个人素材库](media-library.md) 的条目中增加 `editorial.visual`。主件放可编辑的配方或工程，预览放真实渲染，术语和观众常说的描述写进 `terms` 或 `aliases`。风格卡用一幅能看清构图的样图，运镜、光影变化及动效卡可放循环短样。动作白模用中性色和简化物件表现起势、运动、落定；读证据的实装要给关键词留阅读时间。循环演示的复位可以留给看板，交付镜头在原件上停住。

```json
{
  "type": "motion",
  "terms": ["双源收拢", "split screen focus", "把右边的原件放大占满全屏"],
  "prompt": "左右同时出现两个材料。在旁白点名右侧的原件时，分隔线向左移动，右侧连续铺满画面。停在带主语和限定词的原句，读完再切。",
  "samples": {"白模": "运动预览", "见秋实装": "品牌示范"},
  "related": ["case-focus-pair-announcement"]
}
```

`type` 可取 `style`、`motion`、`light`。在每个示范名后填同一条目中的唯一附件标签。完整提示词按需要写画面构成、配色、光线方向、运动主体、时机和停点。涉及真实镜头或可裁切的图片时区分相机移动、数字裁切和焦点变化。两张卡搭配时先用同一内容试两版，例如一张负责档案纸材质，另一张负责量尺与坐标；把各自贡献写入 `parents` 的 `id`、`role`。用见秋的色板、字形和本期证据重做样例。限制使用的原件可以另登记成报道案例，然后用 `related` 回链。参考来源的使用范围随原卡保留。

## 从创作者原站补一张卡

一页枝鸥的片中约 01:49 和 03:06 口播推荐 LetzAI 与 Motion Prompt，约 01:20 的卡片署名另指向 Lemo-Opuscar。片尾是作者的本地图鉴。查目录时优先记具体条目与中文别名；打算使用原件时逐项读许可。LetzAI 的配图看该图 credit；Motion Prompt 的代码和嵌入的第三方媒体分别看项目许可与第三方说明；Lemo 的代码、说明、影片及配乐各查 LICENSE 和作品 CREDITS。给个人库做自有实装时留原件链接、所学之处和自己可改的输入。[Motion Prompt 第三方说明](https://github.com/showheyohtaki/motion-prompt/blob/main/THIRD-PARTY-NOTICES.md) · [Lemo 分项许可](https://github.com/lemomo-ai/lemo-opuscar/blob/main/LICENSE)

找视觉处理的名字时，用 **LetzAI Atlas** 查成像原理和原始例图。比如“蓝晒”指普鲁士蓝的底色和被物件遮住后留下的浅色轮廓，可用自己的零件形状做接触印相示意。条目列出每张图的出处和许可，取用外部图像时按那张图登记。[蓝晒原条目](https://atlas.letz.ai/effect/cyanotype) · [公开只读接口](https://atlas.letz.ai/agents)

找运动的名字时，用**大滝昇平的 Motion Prompt** 看循环示范，核对哪一样变了。“前面虚下去、后面清楚起来”对应 #033 拉焦；让摄影机位置、取景范围保持稳定，只移动清晰的焦平面。“让边缘亮起来”可以比较 #098 轮廓光。原站的多语目录有简体中文，编号可直接回看示范。[拉焦示范](https://showheyohtaki.github.io/motion-prompt/#033) · [轮廓光示范](https://showheyohtaki.github.io/motion-prompt/#098) · [作者项目及许可](https://github.com/showheyohtaki/motion-prompt)

挑整段的美术方向时，**Lemomo 的 Lemo Opuscar** 可以把风格说明与实际成片放在一起看。以钢笔淡彩为例，先观察轮廓用线与色块的分工，再把本期主体画到见秋的版心里。遇到文件取证，保留原文原貌，把插画用在解释段。复用外部文件时分别核对代码、风格文本、演示片及片中第三方材料的条款。[钢笔淡彩案例](https://lemomo-ai.github.io/lemo-opuscar/zh/styles/urban-sketch/) · [项目许可](https://github.com/lemomo-ai/lemo-opuscar/blob/main/LICENSE)

```sh
python3 "$SK/scripts/visual_sources.py" 蓝晒 --source atlas --detail
python3 "$SK/scripts/visual_sources.py" 焦点转移 --source motion --json
python3 "$SK/scripts/visual_sources.py" 钢笔淡彩 --source lemo --detail
python3 "$SK/scripts/visual_sources.py" 轮廓光
```

取能解释本镜的具体条目。用自己的主体试作，先定一张静态风格图，再渲染一次运动。外部贡献写入 `editorial.visual.sources`，每项存 `title`、`url`、`role`；本库已用的动作卡继续写入 `parents`。例如蓝晒提供底色与浅色轮廓，拉焦负责把注意力从前景零件移到后景刻度。看板会把这两个原页放在“参考技法与原站”，下期可以沿着卡片找回准确示范。个人卡可离线调用。Motion Prompt 和 Lemo 的公开目录首次查询后缓存一天；更新失败时可以查上次成功读取的目录，结果中会注明时间。LetzAI 按关键词在线查询。

## 从分镜调用并回写

下列命令在项目目录运行，`SK` 指向安装的 creator-video。选卡前在项目的 screen-plan.json 里确定镜头和起止帧，使用自定义计划名的工程由 edit.json 的 screen_plan 定位。先搜再看卡，需要该卡时将它登记到具体剪点。

```sh
python3 "$SK/scripts/library.py" search --visual motion 双源
python3 "$SK/scripts/visual_case.py" select motion-focus-pair-white --cut-start 0 --shot interim --intent "先看两份文件，再读出右侧公告限定的职务" --settings focus-settings.json
```

select 会把卡的原件和版本化附件提取到项目，向该 cut 的 `visual_cards` 写入卡 ID、版本、意图和 settings。只选风格时可以省略 settings；提取报道专用卡时按实际用法加 `--purpose editorial`。画面脚本从本镜的选择读取参数，使用项目副本及本期来源。通用卡的收拢方向、两端帧应按本句口播重算。把原件的链接、日期和引用范围放在项目来源里。

口播驱动的图层可以参照一页枝鸥公开工作台的做法，先找实际字幕里触发选卡的那句话，再按本片帧率落点。若同镜还有阅读任务，分别标运动到位、原词划线结束、中文字幕消失三处；尤其当读者必须从画面中的原文移回屏幕底部时，给最后一次视线移动留时间。公告示例在字幕层多留了 21 帧，旁白与纸声保持原时点。[作者的 Overlay Studio](https://github.com/jeszhou/overlay-studio)

正常导出随候选冻结所选卡、帧窗与项目实际文件校验值。若专为本镜调整从库里提取的渲染器或主件，先用 `visual_case.py derive 卡ID --cut-start 入点帧 --reason 修改理由` 记录这些文件和上游卡版本。[独立审片](editorial-review.md)结束后，从实际看片意见提炼有下次使用价值的判断，写回对应卡。以下示例中的 MEDIA 是本轮已讨论和裁决的实际编码文件。

```sh
python3 "$SK/scripts/visual_case.py" writeback motion-focus-pair-white "$MEDIA" --cut-start 0 --lesson "两份文件并排用于辨认来源；右栏全屏后保留任命人名、临时职务和出处。"
```

下一期可在看板展开实片审片记录，或运行 `python3 "$SK/scripts/visual_case.py" lessons motion-focus-pair-white` 读取结构化判断。看记录中的画幅和原件再决定是否沿用，尤其留意观众的视线从英文划线回到中文字幕时，该词是否还在。

回写取自项目的 qa/editorial/ledger.json 和候选登记时的选择。看板可展开查看镜头、当时的卡版本、结论及实看证据。返修或暂缓的结论也可留作失败案例。相同裁决重跑复用原记录。搬库时带上 visual-reviews/ 和 learning-results/。
