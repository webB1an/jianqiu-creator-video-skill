"""Build the local, offline listening index from source records."""
from pathlib import Path
import json,html
from soundbank import BANK

def build():
    labels={'shutter':'机械快门','film-wind':'快门与卷片','paper':'滑动纸张','dial':'旋钮按压','swish':'短掠过','page-turn':'翻页','switch':'机械开关'}
    esc=html.escape;rows=[]
    for path in sorted(BANK.glob('*/source.json')):
        r=json.loads(path.read_text());src=esc(r['page'],quote=True)
        title=labels.get(r['tag'],r['tag'])
        rating=(f"★ {r['stars']} / 5 · {r['votes']} 人评分" if r.get('stars') else "未提供平均星级")
        downloads=(f" · {r['downloads']:,} 次下载" if r.get('downloads') is not None else "")
        rows.append(f"""<article><header><h2>{esc(title)}</h2><span>{r['duration']:.2f} 秒</span></header>
        <p>{esc(r['title'])}</p><audio controls preload="none" src="{esc(path.parent.name,quote=True)}/take.wav"></audio>
        <footer>{rating}{downloads}<br><a href="{src}">{esc(r['author'])} · Freesound</a> · CC0</footer></article>""")
    page="""<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>见秋 · 本地音效库</title><style>
    *{box-sizing:border-box}body{margin:0;background:#131a17;color:#f4eddd;font:16px system-ui}main{max-width:950px;margin:auto;padding:56px 24px}h1{font:2.5rem 'Songti SC',serif}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:20px}article{background:#202925;padding:24px;border-top:2px solid #eda961}header{display:flex;align-items:center;justify-content:space-between}h2{margin:0}span,footer,p{color:#c0c3b7}audio{width:100%;margin:8px 0 16px}a{color:#eda961}footer{line-height:1.8}</style><main><h1>见秋 · 本地音效库</h1><section>"""+''.join(rows)+'</section></main></html>'
    (BANK/'catalog.html').write_text(page)
if __name__=='__main__':build()
