---
name: creator-video
description: 根据一句话主题自动调研、写稿、配音、制作动效和声音、剪辑、团队审片、按镜头返修并签发原创 MP4，适用于科技解释、产品故事、人物口述与档案叙事，可为社交平台准备投稿稿件与逐镜重排的竖版，也可交付同题文章与全文朗读。每次制片先检索个人素材和内容，制作中持续补库。也可到视频平台研习多位创作者、整理资产，或逐帧研习参考片并升级通用剪辑方法。
---

# 一句话视频导演

## 核心铁律

- **维护 skill**：用户要求优化本技能时，直接更新本机技能并接入默认制片入口，以真实工程数据、帧坐标、音频时基或像素结果核验，交付写明后续成片变化。仅当用户明确要求制作/试剪/返修影片时再出片；只问能力用法时直接回答。
- **反 PPT 视觉节拍（硬门禁）**：优先使用动态实拍/档案视频与视频铺底（`remotion_effect.background: "assets/broll.mp4"` 自动循环与按亮度自适应压暗），真实视频承载时长原则 ≥35%，严禁连续 3 镜以上纯色静卡。中位镜长控制在 4–7 秒，前 5 秒必有视觉推进或换镜，每 3–5 秒给一次可见变化，每 15–30 秒给一次节奏打断；网页/PDF 用 `screen_motion.py` 保持连续运镜。渲染前过 `prepare.py --preflight` / `--audit`，导出后过 `review.py --critique` 核验静止占比与最长死停。
- **动效选用顺序**：先查本地 43 项 Remotion 库（`scripts/remotion_library.py list 关键词` + [预览网页](assets/remotion-library/index.html)）→ Remotion 官方组件组合（[动效框架](references/motion-frameworks.md#先找现成组件)）→ 定制并[加回本地库](references/remotion-library.md#把可复用动效加回本地库)。
- **先查个人库、每片都沉淀**：检索素材前先用 `project_learning.py --project 本期 find 检索词` 与 `library.py semantic-search` 查[个人素材库](references/media-library.md)，选定与复盘结论用 `decide` / `retention_feedback.py` 回写。专门研习博主或逐帧拆片见[自主整理与动效研修](references/continuous-learning.md)与[带时间码拆片](references/story-sound.md#带时间码拆片)。
- **品牌与交付规格**：正片固定 16:9（默认 1920×1080、30fps），沿用[见秋品牌规格](references/jianqiu.md)与本地 ZipVoice「见秋」多情绪声线（[见秋旁白](references/voicecasting.md)）。成片落 `videos/<主题>/out/final.mp4`，交付正片、字幕、16:9 / 9:16 / 3:4 封面、工程、来源和重建命令。社交投稿见[投稿稿件](references/social-handoff.md)，竖版见[9:16 逐镜重排](references/portrait-cut.md)，同题文章朗读见[全文朗读](references/run.md#文章与全文朗读)。

## 统一命令速查（主干路由）

| 阶段 | 首选入口命令 | 说明与细则参考 |
| :--- | :--- | :--- |
| 开工与模块目录 | `prepare.py --new-edit explainer` + `style_bible.py init --apply-edit`（`studio_pipeline.py catalog` 查五域模块） | 支持 `news`/`explainer`/`story`/`teardown`，自动锁定色板与默认参数；五域门面见 `scripts/studio/`（[生产入口](references/run.md)） |
| 包装与写稿校验 | `packaging.py draft --apply` + `story_craft.py audit .` + `story_craft.py annotate-moods edit.json --write` | 160px 客观对比度门禁、封面 5s 兑现、微型悬念闭环；支持行内韵律 `[pause 0.35]` 与 `*重点词*`（[写稿](references/video-writing.md)） |
| 素材路由与接力质检 | `library.py semantic-search` → `broll_router.py route edit.json` → `media.py search-video/fetch/qc` | 实拍/AI视频/Remotion 三路协同；用 `generate 描述 --chain-from 上一镜.mp4 --out 下一镜.mp4` 接力，生成后自动质检和有限重试（[工艺参考](references/production-craft.md)） |
| 2.5D / 地图 / 数学 / 取证 | `parallax_25d.py` / `geo_map.py` / `math_motion.py` / `capture_passage.cjs` + `screen_motion.py` | Apple Vision 前景抠图 2.5D 视差与人物背后大字、Natural Earth 高精度地图、通用数学曲线与网页原文取证 |
| 配音、J/L-Cut 与预检 | `prepare.py --read-script` → `prepare.py` → `studio_pipeline.py preflight --project . --write --apply-sfx` | 原生 `j_cut`/`l_cut` 声画错位、65+ 科技词+多音字审计、5 组情绪参考音（`voice_mood_pack.py` 可导入真人录带）、视觉媒介多样性闸门 |
| 配乐过门、真拟音与强拍 | `music_stems.py packs/init-plan/mix --beats-out qa/beat-grid.json` → `beat_snap.py --write` | 3 套 CC0 4 轨包自带章节过门与钢琴终止式；`sfx_scaffold.py` 优先调度 CC0 实录拟音；人声 `broadcast`/`studio-master` 内置去齿音 |
| 快审、母带、多模态审片与修补 | `export.py --proxy` → `export.py --stage-only`（技术审片，局部改用 `--patch-shots`）→ `studio_pipeline.py postcheck --media 候选.mp4 --backend auto\|deepseek\|local-vlm\|gemini\|offline` | 540p 节奏快审与定长局部画面修补；审片支持 **DeepSeek API Key**、本地离线 VLM（Ollama/LM Studio/MLX）与免模型的本地视觉审片，竖版支持动态平滑跟焦 |

## 七步标准制片流程

1. **调研、包装先行与导演意图**：核实一手来源，在 `edit.json` 写清 `story_intent`（`kind`/`main_point`/`viewer_value`），按[片种与导演标准](references/direction.md)和[解释片导演](references/explainer-direction.md)锁定贯穿对象与结尾交付。先跑 `style_bible.py init --apply-edit` 锁定全片影调（文字色卡板 `style-bible.png` 供人参考，无字氛围底图 `style-mood-plate.png` 供光影参考），并用 `packaging.py draft --apply` 过 160px 手机缩略图客观对比度与字数门禁。
2. **写口语稿、韵律标注与微型悬念闭环**：按[视频写稿与读稿](references/video-writing.md)写纯口播台词，每 35–45 秒在揭晓前埋下下一个好奇心缺口，消除书面腔与寒暄；可在台词中用 `[pause 0.35]` 插入精确停顿、用 `*重点词*` 同步触发口播微重音与字幕高亮。镜序拿不准时用[实素材粗剪](references/run.md#实素材粗剪)与 `prepare.py --adopt-roughcut` 对照定剪；[视觉卡](references/visual-atlas.md)用于选景与用光参考。
3. **三路素材路由、2.5D 视差与入库质检**：先搜个人库（`project_learning.py find` / `library.py semantic-search`），再以 `broll_router.py route` 分流：① 现实物证走实拍视频（`media.py search-video` + `fetch`，入库跑 `media.py qc` 避开跳切、虚焦与黑边；多候选按[接触帧对齐比较](references/live-action-editing.md#按同一动作点比较候选原段)）；② 抽象/历史意象走风格锁定 AI 视频（`broll_router.py generate` 支持 `--chain-from` 尾帧接力、生成后自动 QC 重试与离线底片回退，首帧严禁传 `style-bible.png`）；③ 静态主视觉照片用 `parallax_25d.py` 生成 2.5D 景深视差与人物背后大字；④ 精密数据与文字走 Remotion 43 项库（自动按底片亮度自适应压暗）、`math_motion.py`、`geo_map.py` 或[可渲染场景](references/explainer-scenes.md)。
4. **科技词与多音字拦截、多情绪配音与统一预检**：用 `prepare.py --read-script` 盲读确认主旨，用 `pronunciation_review.py` 同步拦截多音字与 65+ 科技英文词发音风险，用 `story_craft.py annotate-moods --write` 注入 5 情绪标签（真人 5 情绪母带可用 `voice_mood_pack.py import` 导入），按[口播节奏](references/narration-pace.md)实测语速。可在镜头上配置 `j_cut`/`l_cut` 实现电影感声画错位；运行 `prepare.py` 绑定时间轴后，执行 `studio_pipeline.py preflight --project . --write --apply-sfx` 检查叙事和媒介分布，写入对焦避让、跨镜调色与拟音。预检回写 edit.json 后重跑 prepare.py，再按新时间线重建依赖镜头与配乐。定剪前用 `studio_pipeline.py preflight --require-visual-clear` 定位连续三镜同类和超八秒静停；导出后在候选的导演报告中复核实际画面。
5. **画面合成、音乐过门/终止式、强拍吸附与快审定剪**：普通镜头用 `camera`/`camera_drift`、`fit: backdrop/paper/ink`、`speed`、17 套原生转场、[分层 PNG 动效](references/kinetic-evidence.md#在普通镜头上逐层编排动作)与[空间桥](references/spatial-bridges.md)；完整参数见[画质与音画工艺参考](references/production-craft.md)。字幕默认 `adaptive-kinetic`，人声润色 `broadcast`/`studio-master` 内置去齿音与微空间融合。配乐用 `music_stems.py init-plan --pack <包名>` + `mix --beats-out qa/beat-grid.json`（自带章节过渡过门与钢琴终止式收尾），可选 `beat_snap.py --write` 后重跑 `prepare.py` 吸附强拍。先跑 `export.py --proxy` 生成 540p 快审版定节奏，再出 1080p 母带。
6. **母带导出、多后端多模态审片与独立会审**：用 `export.py --stage-only` 生成技术审片候选，编码校验响度（−16 LUFS）与帧时钟，随后运行 `studio_pipeline.py postcheck --project . --media <候选.mp4> --backend auto`（支持 **DeepSeek API Key** `DEEPSEEK_API_KEY`、本地离线 VLM 如 Ollama/LM Studio/MLX-VLM、Gemini 与免 Key 内置离线视觉审片器）生成导演报告和返修单，按[快审与返修](references/run.md#快审与返修)定位本次结果。竖版派生用 `portrait_cut.py`（开启 `auto_focus: true` 对动态视频自动平滑跟焦摇移）。用 `editorial_review.py begin 候选.mp4` 登记本轮，再按[审片团队](references/editorial-review.md)、[审片责任](references/review-ownership.md)组织至少三位独立审片人会审。
7. **自动修补、定长镜头替换、签发与复盘**：机械问题可用 `multimodal_critique.py patch --tickets ... --apply` 自动修补（支持加运镜、过桥、字幕避让、删空拍、加 J-cut），局部单镜修改可用 `export.py --patch-shots <镜号> --patch-from <旧母带.mp4>` 重渲本镜和关联转场，保留区间外画面及母带音轨，精细返修见[局部返修](references/graphic-voice-revision.md)。发片后用 `retention_feedback.py ingest --save-library` 沉淀复盘数据。

遇到说得出效果却叫不出术语的视觉需求，先查[视觉卡与创作者原站检索](references/visual-atlas.md#从创作者原站补一张卡)；真人主持连续口播走[主持人与词时钟](references/explainer-presenter.md)；可复用动作参照[创作者拆解](references/creator-lessons.md)。发布成片或付费采购由用户决定。
