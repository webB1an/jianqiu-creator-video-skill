# 成片的独立审片、退改和签发

每次导出影片，先审实际候选，再决定交付。普通项目的记录位于 `qa/editorial/ledger.json`，每次裁决都会生成中文 `minutes.md`。主编承担决定和后续执行，用户指定看稿节点时才在该处停下。在项目目录运行命令，`SK`、`PY` 见[运行说明](run.md)。

## 看片和讨论

主编把同一候选的路径、版本 SHA、需求和必要的一手资料给三个独立审片人。可用多代理时开独立工位；也可接收实际同事各自提交的意见。三人先分别返回原始意见，再看彼此的判断。首轮至少一席按正常速度从头看到尾，另外两席按职责回放重点。字幕、素材署名、三种封面、交付入口及复用资产的具名交接见[审片责任与交接](review-ownership.md)。影片较长可分段分工，留有全片连贯性审看。只有一位执行者或没有真实审片条件时，保留候选并说明缺哪一席。不能把同一人编写的三段意见记作三次独立观看。

- **手机观众与叙事**看第一遍会把注意力放在哪里，信息能否读完，开头的提问和结尾的回报能否接上。静音时另看字幕和真实网页按钮是否被读成同一句。
- **画面、动效与事实剪辑**对照画面和原件，检查数字、身份、取景、运动、转场和连续性。对争议帧看原速、前后各一镜，必要时逐帧。主编在开审时明确由哪位独立审片人负责图文排版，通常由画面席兼任。该席先看成片，指出各组文字依附哪个图标、栏格或控件，再对照镜头计划，补出漏列的关系和状态。
- **声音与节奏**对照真实终混，复听旁白、音乐留白、声音过剪点和接触声的时刻。写清试听条件；只有 ASR、分轨、峰值或波形时，标明已验证的声音特征。如果本票专门讨论音色、刺耳或音乐审美，应由实际试听过的人复核。

有配乐、拟音或编辑式运动时，[局部听审与连续帧记录](screen-and-mix-review.md)给出对应观察字段。主编看 status 返回的 missing_perception，再决定是否签发。图形、标签和交互控件按[关联元素的视觉对齐](alignment.md)逐组登记成片测量，缺组或已记录的超差会列入待复核项。含口播的新项目还须在实际交付候选记录前五秒遮住字幕后看到了什么，以及亮、暗帧中字幕周围还看得见什么。字段与实测命令见[开场、浮动字幕与终混](presentation.md)。

图文镜头签发前，主编对照候选画面与该席的逐组记录。手机原速回放证明当时的观看体验，窗外帧一致证明局改边界；此前没有验过的关系，仍须补看。若观众已经指出具体位置，复查同一组件在其他镜头、不同字长和两端状态下的表现，在会审纪要写明检查范围。见[网约车片漏检复盘](alignment.md#网约车片漏检复盘)。

### 主编需要看见的编辑证据

在正式候选的观察条目里加 `editorial`，仍用原来的 `shot / start_frame / end_frame / evidence` 标定实看范围。至少两位独立看片人提交编辑判断，例如观众席复述首尾，事实席对读引文并做删镜比较。独立看出同一问题时可引用已提交的同一张票。每个对象含 `kind` 和 `verdict`（`keep` 或 `fix`），下表为其余字段。首尾的承诺也可以是一种感受、动作结果或产品用途，按作品目的填写。

| kind | 观察什么 | 必填文本 |
| --- | --- | --- |
| opening | 从第 0 帧开始，看第一次揭示。指出它发生在哪个画面，何时值得继续看。 | promise、first_reveal |
| cut | 选一处最可能互换、最值得保留或最容易被声效分散的剪点。删或对调两边再判断损失。单镜头可对比两种连续状态与假设的剪点。 | before、after、without_cut |
| payoff | 看到最后一帧，回述开头的承诺、本片给出的答案或可带走的判断。片末另起话题时，核对连接证据。 | promise、takeaway |
| source | 看原件与同窗成片。原句含上下文，逐句记实际口播和画面强调；原件写条件、否定、转述、身份或数量口径时，检查限定的主语与作用范围。静默画面写清无口播。 | locator、original、spoken、onscreen、comparison |
| source_scope | 素材没有引用、转载判断或需要原件验证的主张时，写明实际拍了什么、凭什么采用这种范围。 | basis |

`screen-plan.json` 中的网页和文件默认进入取证范围，导出把来源标识、片段说明和帧窗随该版封存。纯交互演示、画面未承担引用时，该 cut 可填 `"verify_claim": false`；采访原声、图表、口播等任何其他镜头也可填 `"verify_claim": "需核对的具体主张"`。事实席回看整片，补上计划漏列的关键台词。对于 `status.source_targets` 列出的每个目标，在独立 `source` 观察中填对应的 `target`（例如 `deny@420`），观察帧限于该镜头中展示证据的范围。额外发现的未登记引文可直接补充 `source`。无目标的旧工程仍要审实际引用或说明 `source_scope`。开头、删镜、片尾、原件核对缺哪项都在 `missing_editorial` 列出。

需要改动时，写 `"verdict":"fix","issue_id":"Q01"`，在同一席的 `issues` 给出改动范围与能复核的验收条件，也可指向已提交的重叠票。一处错译若改变意思，给 `source` 记 `critical`，本轮退改或暂缓。审美意见交由主编在会审中决定取舍。原件与台词是否等义、删镜是否更好由看片人判断，工具核对记录、时间窗、票据与版本。已提交独立意见后，可用 `supplement 媒体 补充.json` 补交观察和新增票，已举行的会议会重新打开。

固定时长局改时继续使用原票和 `--patch`。只有在画面、音频、字幕及时间轴能验证相同的窗外，工具才沿用上一轮可保留的指定原件对读；受影响或接缝相邻的引用重新对读。编辑席仍在新片上查看片头、一个剪点与片尾，核对局改是否改变全片承诺。历史签发的 `verify` 仍可校验旧文件，重新签发、发布时用 `begin 候选 --from 旧SHA` 开新轮。

旧候选没有封存镜头范围时，新轮会根据媒体所在工程、登记的时间轴哈希及可用的原编排哈希找回镜头计划。看到 `editorial_scope.origin=matched_legacy_project` 时，按本轮冻结的目标逐一核对。如果旧版本已脱离工程，`legacy_unindexed` 会提示用配套的 `--edit` 和 `--timeline` 重新 stage，或由事实席逐镜盘点。新版候选会显式保存版本及目标数组，已登记的空数组也会随版本保存。

每人写下看过的片段、观察证据和具体影响，可以提交零张修改票。主编把冲突的方案带回给相应席位，记录各方理由、保留项和最终选择。重复发现归并成一票，保留独立证据。票上写稳定镜头 ID、零起算帧区间 [start,end)、责任人、优先级、可观察的通过条件。区分画面、字幕、声音、跨镜转场。安排修订时补充可修改的组件或源文件、锁住的轨道与对照方法。每轮都留存候选和意见。

## 最小修改范围

- 新建镜头时保留稳定 ID、原素材、干净画面、字幕、声音分轨、起止帧和可独立重跑的源码/参数。Remotion 的一个 Sequence、一个图表的高亮位置、一个字幕 cue 都可以独立处理。原生工程用 `remotion render … --frames=A-B` 时 B 包含在内，换算工程内本镜的局部帧，统一交付区间使用右开端点。跨镜动效把真实重叠区、音尾或遮挡过切点纳入同一票。
- 固定时长的画面修改，只重新渲染该段的干净画面。品牌、字幕使用与全片相同的合成器与绝对时间。镜头上已有最终字幕及标记时直接拼接，避开二次烧录。改外部 SRT 时也检查片内烧录是否对应。
- 只改某一处音乐或拟音时，从原工程分轨重混该票的短窗口，包含已有口播、其他声部和需要的音尾。音频工具接受完整的局部混音 WAV。原视频包全数复制。AAC 的帧重叠可能在接缝外产生微小差异，逐次看报告；`--require-exact-exterior` 用于需要接缝保护区外逐样本相等的情况。
- 改文案长度或剪点顺序时，先重算受影响的区间和依赖，继续复用未改的配音、素材及独立镜头。若变更确实波及全片的时基、混音或品牌，就在票里记录影响范围，再走新候选和复审流程。

普通导出在实际镜头起点放 H.264 关键帧。图片拼接工具适用本流程的恒定帧率 H.264、8 位 yuv420p、方形像素 MP4。遇到不兼容信号或边界时按工具反馈改用源工程和实际剪点，在修改票中重新界定受影响区域。先用 --plan 查看可独立拼接的帧边界。旧片被编码依赖卡住时，工具会返回最小可行区间；主编确认票的范围后再渲染这个镜头，并复看扩入的部分。拼接后同时验证顺序解码和随机跳转。画面票验整段外解码像素及音频包和时点，声音票验全部视频包、时间戳、实际 AAC 接缝和窗外误差。

## 操作

```sh
"$PY" "$SK/scripts/export.py"                   # 输出登记的候选路径
"$PY" "$SK/scripts/review.py"                   # 当前候选的技术抽查，可加 --asr
G="$SK/scripts/editorial_review.py"
"$PY" "$G" status "$MEDIA"
"$PY" "$G" observe "$MEDIA" viewer.json
"$PY" "$G" observe "$MEDIA" picture.json
"$PY" "$G" observe "$MEDIA" sound.json
"$PY" "$G" discuss "$MEDIA" meeting.json
"$PY" "$G" decide "$MEDIA" decision.json
```

各 JSON 的 `candidate_sha256` 填 status 返回的完整 SHA-256。对相同 MP4 更换封面或外部字幕时，用 `--from` 开新轮，并在各份意见里写新的 `round` 轮号。覆盖完整审片窗口的 `observations` 可拆成若干实看区间。

| 文件 | 其余字段 |
| --- | --- |
| 每席观察 | reviewer、role、viewing、observations、issues。每条观察含 shot、start_frame、end_frame、evidence。每张票另含 id、priority（critical / major / minor）、owner、acceptance。 |
| 会议 | chair、discussion（每位的 reviewer、point）、tickets（id、action、reason）。决定 fix 时再填 implementer，用实际执行人或代理的稳定名称。可用 checker 预派本轮已看片的独立复核人；省略时，下一轮的 check.reviewer 登记实际复核人。 |
| 复核 | id、reviewer、result 为 pass 或 fail、evidence。预派人改由他人接手时，新复核人写 handoff_reason。固定时基局改复看区间须覆盖原票；时轴重排时用新起止帧和 remap_reason 交代对应关系。 |
| 裁决 | chair、decision 为 deliver / revise / hold、reason。deliver 需要所有安排修改的票通过、三席完成讨论，并成功解码到底。 |

固定时长画面票的典型命令，A/B 为全片的帧号。CLEAN 是该镜头重渲的精确长度素材。

```sh
"$PY" "$SK/scripts/targeted_edit.py" "$MEDIA" --start-frame "$A" --end-frame "$B" --plan
"$PY" "$SK/scripts/render_window.py" "$CLEAN" --start-frame "$A" --end-frame "$B" --output edit/window.mp4
"$PY" "$SK/scripts/targeted_edit.py" "$MEDIA" edit/window.mp4 --start-frame "$A" --end-frame "$B" --expect-base "$OLD_SHA" --output edit/v2.mp4 --report edit/change.json
```

声音票用下列命令代替以上两行。WAV 跟原片的声道、采样率与窗口样本数一致。

```sh
"$PY" "$SK/scripts/audio_window.py" "$MEDIA" edit/mixed-window.wav --start-frame "$A" --end-frame "$B" --expect-base "$OLD_SHA" --output edit/v2.mp4 --report edit/change.json
```

```sh
NEW=$("$PY" "$SK/scripts/review_bundle.py" edit/v2.mp4 --sidecars "$SIDECARS" --timeline timeline.json)
"$PY" "$G" begin "$NEW" --from "$OLD_SHA" --patch edit/change.json
# 三席在新文件上复看受影响范围和前后接缝，各自提交新 observe；用窗外实测决定可沿用哪些判断。
"$PY" "$G" check "$NEW" recheck.json
"$PY" "$G" discuss "$NEW" meeting-v2.json
"$PY" "$G" decide "$NEW" decision-v2.json
"$PY" "$G" publish "$NEW" --output out/final.mp4
"$PY" "$G" verify out/final.mp4
```

SIDECARS 为该版要交付的 captions.srt 和 covers/ 所在目录。默认使用上一版的配套文件，确有字幕或封面修改时先在单独目录准备。审核失败就带着未关闭的票继续下一轮；主编可暂缓并写明需要补的证据。通过后将候选 MP4、签过的字幕和三种封面一同提升为正式版。文件、票和决定均按本轮内容校验，工程整体搬家仍可用。交付时提供观看链接和本片纪要。

新片已在 edit.json 记录 story_intent 时，开轮冻结主旨和预期的观众收获。主编导入同版独立首看，讨论逐条对照观众复述、具体的注意力变化与成片，形成签发依据。新闻片尤其要查镜头里的例子怎样支持“发生了什么、为什么与我有关”。命令、退改条件和人物片例子见[首看与整片主旨](review-ownership.md#看懂了示例能否讲出整条片)。

### 句尾疑点的处置

含新版逐句记录的工程先对该候选运行 review.py。默认报告位于候选旁的 qa/content-check.json；使用自选目录时，在裁决 JSON 的 ending_report 指向该报告。它绑定 MP4、时间线和 SRT 的摘要。voice_endings 按时间顺序记录干声及终混的尾词比对，编号从 1 起算。本机缺识别模型时会记录未核验。英文拼写波动和同音字留给听者判断。

遇到 possible_missing_ending，先取同一候选完整的一句与下一拍。如果确实丢了尾字，重录这一句并重建受影响的剪点；干声完整而终混少字时，检查混音截取与转场。若听者确实能辨出最后几个字，在本轮独立意见的一条 observation 内填写 `"ending_listen":{"sentence":3,"result":"complete","heard_tail":"末班车","listening_method":"用耳机从正式片该句起点原速听过剪点"}`。观察帧窗覆盖整句。写实际听到的词与试听条件。把识别文本、峰值或增加静音当作恢复字音，会留下同一张返修票。带疑点的句子须有同版听辨记录或经修订复核，方可签发。
