import io, struct, zipfile, sys

APK = r'C:\Users\86177\Desktop\轻简虚拟环境开发\dist\via-7.3.3.apk'
z = zipfile.ZipFile(APK)
dex = z.read('classes.dex')

def u4(b, o): return struct.unpack_from('<I', b, o)[0]
def u2(b, o): return struct.unpack_from('<H', b, o)[0]

H = {
    's_size': u4(dex, 0x38), 's_off': u4(dex, 0x3C),
    't_size': u4(dex, 0x40), 't_off': u4(dex, 0x44),
    'm_size': u4(dex, 0x58), 'm_off': u4(dex, 0x5C),
    'c_size': u4(dex, 0x60), 'c_off': u4(dex, 0x64),
}

def read_uleb128(b, o):
    r = 0; s = 0
    while True:
        byte = b[o]; o += 1
        r |= (byte & 0x7f) << s
        if not (byte & 0x80): break
        s += 7
    return r, o

strings = []
for i in range(H['s_size']):
    off = u4(dex, H['s_off'] + i * 4)
    _, p = read_uleb128(dex, off)
    end = dex.index(b'\x00', p)
    strings.append(dex[p:end].decode('utf-8', 'replace'))

types = [strings[u4(dex, H['t_off'] + i * 4)] for i in range(H['t_size'])]

methods = []
for i in range(H['m_size']):
    base = H['m_off'] + i * 8
    methods.append((types[u2(dex, base)], strings[u4(dex, base + 4)]))

classes = []
for i in range(H['c_size']):
    base = H['c_off'] + i * 32
    classes.append((types[u4(dex, base)], u4(dex, base + 24)))

def parse_class_data(cd_off):
    out = []
    p = cd_off
    sf, p = read_uleb128(dex, p); inf, p = read_uleb128(dex, p)
    dm, p = read_uleb128(dex, p); vm, p = read_uleb128(dex, p)
    for _ in range(sf): _, p = read_uleb128(dex, p); _, p = read_uleb128(dex, p)
    for _ in range(inf): _, p = read_uleb128(dex, p); _, p = read_uleb128(dex, p)
    for _ in range(dm + vm):
        mi, p = read_uleb128(dex, p); _, p = read_uleb128(dex, p); co, p = read_uleb128(dex, p)
        if co: out.append((mi, co))
    return out

def const_strings(code_off):
    """返回 (按出现顺序的 const-string 列表, 以及对 invoke 方法名的粗解析)"""
    insns_size = u4(dex, code_off + 12)
    base = code_off + 16
    found = []
    i = 0
    while i < insns_size:
        o = base + i * 2
        if o + 2 > len(dex): break
        op = dex[o]
        if op == 0x1a:
            idx = u2(dex, o + 2)
            if idx < len(strings): found.append(strings[idx])
            i += 2
        elif op == 0x1b:
            idx = u4(dex, o + 2)
            if idx < len(strings): found.append(strings[idx])
            i += 3
        elif 0x6e <= op <= 0x72 or 0x74 <= op <= 0x78:  # invoke-*
            midx = u2(dex, o + 2)
            if midx < len(methods):
                cls, nm = methods[midx]
                found.append('CALL %s.%s' % (cls.strip('L;').replace('/', '.'), nm))
            i += 3
        elif op in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09,
                    0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19):
            i += 2
        else:
            i += 1
    return found

# 目标方法（类名后缀, 方法名）
TARGETS = [
    ('d9.q$c', 'shouldInterceptRequest'),
    ('d9.q', 'E'),
    ('r8.d', None),
    ('u9.d', None),
    ('e9.u', None),
]

dumped = 0
for cls, cd_off in classes:
    if not cd_off: continue
    cn = cls.strip('L;').replace('/', '.')
    for tcls, tmn in TARGETS:
        if cn.endswith(tcls):
            try:
                for mi, co in parse_class_data(cd_off):
                    mn = methods[mi][1] if mi < len(methods) else '?'
                    if tmn and mn != tmn: continue
                    ss = const_strings(co)
                    if not ss: continue
                    # 只打印含关键内容的
                    if not any(('homepage' in s or 'file://' in s or 'data:' in s
                                or 'text/html' in s or 'css' in s.lower()
                                or 'CALL' in s and ('WebView' in s or 'Mime' in s or 'Asset' in s))
                               for s in ss):
                        continue
                    print()
                    print('  ===== %s.%s  (%d 条) =====' % (cn, mn, len(ss)))
                    for s in ss[:40]:
                        print('      ' + (s if len(s) <= 150 else s[:150] + '...'))
                    dumped += 1
                    if dumped >= 6: sys.exit(0)
            except Exception:
                pass
print()
print('  共 dump %d 个方法' % dumped)
