# 见秋旁白

新片默认用 ZipVoice 在本机 CPU 上把新稿读成「见秋 · 清爽温和青年男声」。直接按 [制作入口](run.md) 运行 prepare.py，edit.json 只需填写每句 text。音色 ID 为 `jianqiu-zipvoice`。明确指定的 line.audio 优先，timeline.json 的 voice_takes 逐句记录用了哪个声音或录音文件。

[用户提供的完整样音](../assets/voices/jianqiu/original.mp3) 来自 BreezeBlue，长 13.416 秒。实际合成使用[开头 4.38 秒的参考片段](../assets/voices/jianqiu/prompt.wav)，逐字稿为「我是见秋。早上九点，我的手机会收到这样一条消息。」[音色配置](../assets/voices/jianqiu/voice.json) 保存样本摘要、截取位置、逐字稿及语速。迁移时连同 assets/voices/jianqiu 一起复制。

脚本使用 sherpa-onnx 的 ZipVoice Distill 中英双语 INT8 模型和 24 kHz Vocos，8 步，声线基础语速为 1.04，新闻和深度解释如何按普通句、数字句和定向读音选速，见[实测语速与留白](narration-pace.md)。首次合成会下载并校验模型，缓存于 ~/.cache/creator-video-models/sherpa-onnx-zipvoice-distill-int8-zh-en-emilia。后续只读取本机文件。已有离线文件时，setup_zipvoice.py 可接收 --archive 和 --vocoder。[模型说明](https://k2-fsa.github.io/sherpa/onnx/tts/zipvoice.html)

按一口气能讲清的意思分句，保留问句和转折的语调。需要调整的句子可设 line.speed，人名、英文缩写或数字可用 line.spoken 单独写读法，字幕继续使用 text。以生成录音的真实长度安排镜头和音乐。

遇到容易混听的词，先从交付 MP4 截出完整一句，找同片中同词的另一处，再对照未混音的录音。语音转写能帮忙定位疑点；同音字转写有分歧时把原声留给听审。若词义在这里确实容易听岔，可用更口语的等义说法只重录这句。同步改烧录字幕和 SRT，核实重录落在原镜内，再从新版最终音轨抽出同一段做前后试听。读音和自然度的结论要注明谁实际听过。

### 一句话有没有读完

运行 prepare 时，有本地 SenseVoice 模型便会逐句比较干声和真实播读稿 spoken。识别到前文连续对应、结尾少了字词时，生成器只为这一句另存试音。先保留引擎合成的停顿，再尝试稍慢的读法，必要时按原参数再读一次。选用能复现尾词、整句匹配度也达标的版本，然后按实测样本长度重新排字幕和剪点。原文件留在 audio/lines；自备录音只做检查。重复 prepare 会核对已选录音的 SHA-256。已有签发片继续使用当时的声音。

在 timeline.json 的 voice_takes 中，每句有 spoken、起止采样及 ending_check。matched 表示转写匹配尾词，uncertain 表示字词无法确定，possible_missing_ending 是听审疑点。asr_unavailable 表示本机缺少模型，转写检查未执行。听审时先听完整干声，再听同一句最终混音。若干声已缺词，就调整本句读法或换用重录的 line.audio；若干声完整，则查旁白拼接、混音和导出的相同时间窗。同音字、轻声和专名要由听过原音的人判断。加长句后停顿可以给观众留出时间，补回缺失的音节需要重录。

审片时运行 `review.py --media 候选.mp4 --listen-term 移液器`。将词换成本片的人名、缩写、数字或单位，可重复传入。加 `--asr 模型目录` 时，字幕和自动转写有字词差异的句子也会列出。在 `content-check.json → listening.page` 打开本地听审页，先听整句再展开文字。每段取自同一候选的最终混音，保留声道、配乐、前后短暂停顿和片内时间。相同词在多处出现时逐处对照。用完整标题或读数修饰词核对含义；转写相同的字也可能读错声调。新时间轴按实际录音的整句取样，旧片或外部 SRT 按交付字幕单元取样。分享听审页时一并复制同目录的 listening-clips。

单独试听时，写一个 voice.json，如 `[{"id":"opening","text":"同一勺热汤，倒进宽碗，为什么凉得更快？"}]`。执行下面的命令得到 audio/takes/opening.wav，voicecast.json 同时记录参考样本摘要、语速和实际时长。

```sh
"$HOME/.cache/creator-video-env/bin/python" "$HOME/.codex/skills/creator-video/scripts/voicecast.py" voice.json --out audio/takes
```

## 指定其他声线时使用 Kokoro

在 creator-video 使用的 Python 3.12 环境中使用项目已有的 numpy 和 sherpa-onnx。模型约 140 MiB。以下命令会校验归档的 SHA-256，再解包。

```sh
mkdir -p "$HOME/.cache/creator-video-models/kokoro-v1.1"
voice_tmp="$(mktemp -d)"
voice_tar="$voice_tmp/kokoro-int8-multi-lang-v1_1.tar.bz2"
curl --fail --location --retry 2 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_1.tar.bz2' -o "$voice_tar"
[ "$(shasum -a 256 "$voice_tar" | cut -d' ' -f1)" = 259c9c8bc906d6078e4e0570421885dabb68b539c2f970fcbb890a58eeaf3e9c ] && tar xjf "$voice_tar" -C "$HOME/.cache/creator-video-models/kokoro-v1.1" --strip-components=1
```

Python 环境用 skill 的 scripts/requirements.txt。将一条或多条完整口播写成 JSON 数组，保存在项目自己的 voice.json，例如以下两句。

```json
[
  {"id":"question","text":"为什么有人多等了十分钟？","speaker":59,"speed":1.18},
  {"id":"answer","text":"把时刻表叠起来，答案就出来了。","speaker":59,"speed":1.23}
]
```

在项目目录生成音轨：

```sh
"$HOME/.cache/creator-video-env/bin/python" "$HOME/.codex/skills/creator-video/scripts/voicecast.py" voice.json --voice kokoro --out audio/takes
```

将 edit.json 的 line.audio 指向 audio/takes/question.wav 等实际文件，再运行 prepare.py。音色 ID 59 为 zm_010，3 为 zf_001，62 为 zm_013。完整音色表可在推理项目试听。text 保留供审稿和回译的文本，spoken 可单独指定中文读法。生成记录在 audio/takes/voicecast.json，含音色、语速、实际秒数与 WAV 摘要。音色决定后尽量整片一致，特别难读的专名可为同一句录制两个 take 重选。

模型作者公开了专业中文语料授权说明，Kokoro 权重使用 Apache-2.0。发布音频时按项目实际使用的引擎、模型与音色记录来源。[Kokoro 中文模型说明](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh) · [sherpa 模型及各音色样本](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_1.html)

### 多音字、短尾音与最终成片

#### 为什么 ZipVoice 会读错多音字（根因与三类雷区）

本机 ZipVoice 中文前端（sherpa-onnx `phrase-matcher.cc`）没有上下文神经 G2P，而是按标点断句后对 `lexicon.txt` 做**前向最长贪婪匹配（最大 10 字）+ 单字默认音回退**。因此多音字读错几乎全部来自三类可预测的机制：

1. **跨词贪婪误切（`cross_word_collision`）**：前一个词的尾字与后一个多音词的首字凑成了词表里的另一个词，导致前向匹配抢先切走。典型真例：「搭出行列式」被切成 `搭 + 出行(chū xíng) + 列 + 式`（吞掉原本在词表里的 `行列式 háng liè shì`）；「公理干不干净」被切成 `干(gàn) + 不干(bú gàn) + 净`（吞掉 `干净 gān jìng`）；「结束了当天」被切成 `结束 + 了当(liǎo dàng) + 天`。
2. **词表缺复合词，回退到单字默认音（`single_char_fallback`）**：ZipVoice 单字默认音对很多高频多音字偏向另一读音——例如 `行=xíng`、`重=zhòng`、`长=zhǎng`、`干=gàn`、`差=chà`、`切=qiè`、`调=diào`、`弹=dàn`、`乐=lè`、`校=xiào`。一旦新复合词不在 `lexicon.txt` 中（如「万行」「链长」「多长」「重算」「重写」「切换」「调参」「弹窗」「背景乐」），引擎就会直接套用错误的单字默认音。
3. **上游词表默认音本身偏误或随语境变化（`wrong_lexicon_default`）**：例如上游 `lexicon.txt` 将「相差/反差/落差/温差/时差」标为 `chà`（标准普通话为 `chā`）、「一行/三行/五行」标为 `xíng`、「总长」标为 `zǒng zhǎng`、「倒数」标为 `dào shǔ`、「净得」标为轻声 `de`。

#### 多音字四步防错闭环

1. **配音前自动词表预检（必跑）**：写完 `edit.json` 后、运行 `prepare.py` 之前，先执行词表切分审计（`prepare.py --audit` 也会内置执行同一检查）：
   ```sh
   "$HOME/.cache/creator-video-env/bin/python" "$HOME/.codex/skills/creator-video/scripts/pronunciation_review.py" --project .
   ```
   工具会模拟 ZipVoice 真实前向最长匹配，逐句报出三类风险（`cross_word_collision`、`wrong_lexicon_default`、`single_char_fallback`）以及对应的默认音素和修正建议。
2. **明确语境自动保底 + 歧义词显式锁定 `pronunciation.phones`**：`prepare.py` 会先通过 `with_context_directions` 对无歧义语境（如「行列式」「万行/三行代码」「重写/重算/重构」「链长/帧长/多长」「干不干净」「相差/反差」「切换/切回」「调优/调参」「弹窗」「背景乐」等）自动注入标准音素，且不会覆盖作者手写的 `pronunciation.phones`。其余命中风险或语境敏感词（如「一行人」vs「一行字」、「总长」、「倒数」、「重读」），一律在对应句子的 `pronunciation.phones` 中显式锁定完整词组：
   ```json
   {"text":"从10月6日搭出行列式，到10月20日切入素数求和，","spoken":"从十月六日搭出行列式，到十月二十日切入素数求和，","pronunciation":{"phones":{"行列式":"h0 ang2 l0 ie4 sh0 i4"}}}
   ```
   `DirectedVoice` 会为每个定向词组生成独立的临时单字别名并写入工程内词表，**从根源上阻断跨词贪婪粘连**，同时正文与字幕继续使用 `text`。**禁止**为了绕开报错把句子改绑为未经 `DirectedVoice` 生成的裸 `line.audio`——那样会跳过自动保底与定向词表。
3. **合成后用音素追踪核验，绝不单靠 ASR 判读音**：SenseVoice 等中文 ASR 具备语言模型纠错，常把读错的 `zhǎng/cháng`、`zhòng/chóng`、`gàn/gān` 自动转写成正确汉字，**只看 ASR 相似度 95%+ 无法发现多音字错读**。必须：
   - 对所有改动句用 `JianqiuVoice(pronunciation_lexicon=..., trace_phonemes=True)` 核对引擎实际送入模型的音素序列；
   - 查看 `timeline.json → pronunciation_review` 与 `content-check.json → pronunciation_review` 确认 `lexicon_risks` 为空或已逐项处置。
4. **成片终混抽听复核**：导出候选后运行 `review.py --media 候选.mp4 --listen-term <多音词>` 抽出最终混音里的完整原句，由人工实听确认声调与自然度；两个读音仅差声调时，可叠加基频曲线对照同声线正反参照。`require` 字段用于复核关键读词或金额的完整性（嵌套数组接受近音转写）。

句末最后一个字常被识别器提前猜中。干声检查会同时保存最后一个识别时间戳到有效声尾的距离、尾音离文件边界的余量。小于约 0.10 秒且声音紧贴文件末尾会进入复查，这个量受识别器时间分辨率和具体音节影响。对“票”这样的复合韵母，观察送气之后的有声音节能否展开和衰减，再跟同一说话人的完整“票”比较。

可以在句子的 `pronunciation` 配置里写 `"ending":"票","guard":"我们继续","speeds":[0.97,0.91,0.86],"min_ending_seconds":0.12`。系统将最后一句接着念完提示载句，从两句间真实低能量区取切点，只留下正片台词。它重新检查最终片段中的末词、有效声尾和切点，随后按真实时长重建旁白、字幕与镜头时钟。原始长录音、选中片段、音素和听审线索保存在 `audio/pronunciation/`。导出后从候选的最终音轨提取同一句，核对声音有没有被剪掉，检验最终金额和句尾，再记录终混与干声的对齐。
