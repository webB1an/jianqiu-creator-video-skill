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

新工程遇到“重写”“三行数字”“长任务”等明确语境时，prepare 会应用已登记的声韵调。对“重读”“一行人”等需要结合含义判断的词会列为审听项。项目显式提供的录音或发音指示优先。

普通话中的“重写”读 chóng xiě，“三行数字”读 sān háng。相同汉字也能代表其他读音。准备后查看时间线的 `pronunciation_review`，它逐句列出可能的歧义和原录音。根据上下文检查模型词典的实际音素，尤其留意“了当”这样的跨词错误切分。review 会把清单放进本次候选的 `content-check.json`，同时保留原版的逐句终混回译。

要保留原文且指定读音，可以给目标句写 `pronunciation.phones`。下面的音素来自当前 ZipVoice Mandarin 词表，`h0 ang2` 对应 háng。普通合成流程会在工程内建立独立词表，把该句目标词锁定到这些声母、韵母与声调。正文、字幕继续使用 `text`。

```json
{"text":"我把三行放在一起看。","spoken":"我把三行放在一起看。","pronunciation":{"phones":{"三行":"s0 an1 h0 ang2"},"require":[["三行","三航"],"一起看"]}}
```

`require` 用于复核关键读词或金额的完整性。一个嵌套数组接受若干近音转写。读音依据要另外保留原生词典命中的音素，或同声线正误读音对听。可用 `JianqiuVoice(pronunciation_lexicon=..., trace_phonemes=True)` 让本机引擎打印实际 token。需要声学比较时截取目标字的时间窗，保留足够的前后文，以相同办法提取正反参照。两个读音只在声调上有别时，还应核对基频走势。

句末最后一个字常被识别器提前猜中。干声检查会同时保存最后一个识别时间戳到有效声尾的距离、尾音离文件边界的余量。小于约 0.10 秒且声音紧贴文件末尾会进入复查，这个量受识别器时间分辨率和具体音节影响。对“票”这样的复合韵母，观察送气之后的有声音节能否展开和衰减，再跟同一说话人的完整“票”比较。

可以在句子的 `pronunciation` 配置里写 `"ending":"票","guard":"我们继续","speeds":[0.97,0.91,0.86],"min_ending_seconds":0.12`。系统将最后一句接着念完提示载句，从两句间真实低能量区取切点，只留下正片台词。它重新检查最终片段中的末词、有效声尾和切点，随后按真实时长重建旁白、字幕与镜头时钟。原始长录音、选中片段、音素和听审线索保存在 `audio/pronunciation/`。导出后从候选的最终音轨提取同一句，核对声音有没有被剪掉，检验最终金额和句尾，再记录终混与干声的对齐。
