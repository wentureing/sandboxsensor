import os, re, sys, glob
from lxml import html as LH

base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_ref', 'aosp', 'raw')
os.makedirs(base, exist_ok=True)

for f in sorted(glob.glob(os.path.join(base, '*.html'))):
    raw = open(f, 'rb').read()
    doc = LH.fromstring(raw)
    for bad in doc.xpath('//script|//style|//nav|//footer|//header'):
        bad.getparent().remove(bad)
    # devsite 正文
    nodes = doc.xpath('//article') or doc.xpath('//main') or [doc]
    text = '\n'.join(n.text_content() for n in nodes)
    text = re.sub(r'[ \t\u00a0]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = [l.strip() for l in text.split('\n')]
    text = '\n'.join(l for l in lines if l)
    out = f[:-5] + '.txt'
    open(out, 'w', encoding='utf-8').write(text)
    print(f'{os.path.basename(f):32s} {len(raw):>8} -> txt {len(text):>7} 字符')
