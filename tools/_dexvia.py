import io, struct, sys, os

APK = r'C:\Users\86177\Desktop\轻简虚拟环境开发\dist\via-7.3.3.apk'

import zipfile
z = zipfile.ZipFile(APK)
dex = z.read('classes.dex')

# ---------------- DEX 解析 ----------------
def u4(b, o): return struct.unpack_from('<I', b, o)[0]
def u2(b, o): return struct.unpack_from('<H', b, o)[0]

header = {
    'string_ids_size': u4(dex, 0x38), 'string_ids_off': u4(dex, 0x3C),
    'type_ids_size':   u4(dex, 0x40), 'type_ids_off':   u4(dex, 0x44),
    'proto_ids_size':  u4(dex, 0x48), 'proto_ids_off':  u4(dex, 0x4C),
    'field_ids_size':  u4(dex, 0x50), 'field_ids_off':  u4(dex, 0x54),
    'method_ids_size': u4(dex, 0x58), 'method_ids_off': u4(dex, 0x5C),
    'class_defs_size': u4(dex, 0x60), 'class_defs_off': u4(dex, 0x64),
}
print('  DEX: strings=%d types=%d protos=%d fields=%d methods=%d classes=%d' % (
    header['string_ids_size'], header['type_ids_size'], header['proto_ids_size'],
    header['field_ids_size'], header['method_ids_size'], header['class_defs_size']))

def read_uleb128(b, o):
    r = 0; s = 0
    while True:
        byte = b[o]; o += 1
        r |= (byte & 0x7f) << s
        if not (byte & 0x80): break
        s += 7
    return r, o

# 字符串表
strings = []
for i in range(header['string_ids_size']):
    off = u4(dex, header['string_ids_off'] + i * 4)
    # string_data_item: uleb128 utf16_size + utf8
    _, p = read_uleb128(dex, off)
    end = dex.index(b'\x00', p)
    strings.append(dex[p:end].decode('utf-8', 'replace'))

# 类型表
types = []
for i in range(header['type_ids_size']):
    types.append(strings[u4(dex, header['type_ids_off'] + i * 4)])

# 方法表
methods = []
for i in range(header['method_ids_size']):
    base = header['method_ids_off'] + i * 8
    cls_idx = u2(dex, base)
    proto_idx = u2(dex, base + 2)
    name_idx = u4(dex, base + 4)
    methods.append((types[cls_idx] if cls_idx < len(types) else '?', strings[name_idx]))

# 类定义表
classes = []
for i in range(header['class_defs_size']):
    base = header['class_defs_off'] + i * 32
    class_idx = u4(dex, base)
    class_data_off = u4(dex, base + 24)
    classes.append((types[class_idx] if class_idx < len(types) else '?', class_data_off))

print('  解析完成')

# ---------------- 遍历 class_data，找方法里的 const-string ----------------
targets = ['homepage2.html', 'homepage.html', 'homepage.css', 'file://', 'data:text/html',
           'shouldInterceptRequest', 'setWebViewClient', 'loadUrl']

hits = []   # (class, method, [strings])

def parse_class_data(cd_off):
    """返回 [(method_idx, code_off)]"""
    out = []
    p = cd_off
    sf, p = read_uleb128(dex, p); inf, p = read_uleb128(dex, p)
    dm, p = read_uleb128(dex, p); vm, p = read_uleb128(dex, p)
    for _ in range(sf):  # static fields
        _, p = read_uleb128(dex, p); _, p = read_uleb128(dex, p)
    for _ in range(inf):  # instance fields
        _, p = read_uleb128(dex, p); _, p = read_uleb128(dex, p)
    for _ in range(dm):  # direct methods
        mi, p = read_uleb128(dex, p); _, p = read_uleb128(dex, p); code_off, p = read_uleb128(dex, p)
        if code_off: out.append((mi, code_off))
    for _ in range(vm):  # virtual methods
        mi, p = read_uleb128(dex, p); _, p = read_uleb128(dex, p); code_off, p = read_uleb128(dex, p)
        if code_off: out.append((mi, code_off))
    return out

def const_strings_in_code(code_off):
    """code_item: header 16 bytes, then insns"""
    insns_size = u4(dex, code_off + 12)
    insns_off = code_off + 16
    found = []
    i = 0
    while i < insns_size:
        o = insns_off + i * 2
        if o + 2 > len(dex): break
        op = dex[o]
        if op == 0x1a:      # const-string, format 21c
            idx = u2(dex, o + 2)
            if idx < len(strings): found.append(strings[idx])
            i += 2
        elif op == 0x1b:    # const-string/jumbo, format 31c
            idx = u4(dex, o + 2)
            if idx < len(strings): found.append(strings[idx])
            i += 3
        elif op in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09):
            i += 2          # move/from16 etc 2 units
        elif op == 0x13 or op == 0x14 or op == 0x15:
            i += 2
        elif op == 0x16 or op == 0x17 or op == 0x18 or op == 0x19:
            i += 2
        else:
            i += 1
    return found

print()
print('  === 扫描 method 里的 const-string ===')
scanned = 0
for cls, cd_off in classes:
    if not cd_off: continue
    try:
        for mi, code_off in parse_class_data(cd_off):
            scanned += 1
            ss = const_strings_in_code(code_off)
            if not ss: continue
            hot = [s for s in ss if any(t in s for t in targets)]
            if hot:
                cn = cls.strip('L;').replace('/', '.')
                mn = methods[mi][1] if mi < len(methods) else '?'
                hits.append((cn, mn, hot))
    except Exception as e:
        pass

print('  扫描方法数: %d，命中: %d' % (scanned, len(hits)))
print()
for cn, mn, hot in hits[:40]:
    print('  --- %s.%s' % (cn, mn))
    for s in hot[:8]:
        print('        "%s"' % (s if len(s) <= 130 else s[:130] + '...'))
