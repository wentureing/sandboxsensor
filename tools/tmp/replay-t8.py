# 用 Python 重放 InitTraceTest#T8 的字节构造与 collectRecords 循环，验证是否死循环。
methods = [(0x10, "com.A", "go")]
records = [(0x10, 1), (0x11, 1)]
rec_size = 14

header = "ART SLOW v3\n*methods\n"
for (mid, cls, fn) in methods:
    header += "0x%x\t%s\t%s\n" % (mid, cls, fn)
header += "*end\n"

data = bytearray(header.encode())
data += b"SLOW"
head = bytearray(28)
head[12] = rec_size & 0xFF
head[13] = (rec_size >> 8) & 0xFF
head[14] = (rec_size >> 16) & 0xFF
head[15] = (rec_size >> 24) & 0xFF
data += head
for (mid, tid) in records:
    r = bytearray(rec_size)
    r[0] = tid & 0xFF
    r[1] = (tid >> 8) & 0xFF
    r[2] = mid & 0xFF
    r[3] = (mid >> 8) & 0xFF
    r[10] = 0x39
    r[11] = 0x05
    data += r

# textEnd: 找 *end\n 的 * 下标
te = -1
i = 0
while i + 4 < len(data):
    if data[i:i+5] == b"*end\n":
        te = i
        break
    i += 1
print("textEnd =", te)

blob_off = te + 5
rec_size_read = int.from_bytes(data[blob_off+16:blob_off+20], "little", signed=True)
print("blobOff =", blob_off, "recSize =", rec_size_read)

start = blob_off + 32
off = start
total = 0
guard = 0
while off + rec_size_read <= len(data):
    total += 1
    off += rec_size_read
    guard += 1
    if guard > 10000:
        print("DEAD LOOP: recSize 可能是 0 或负数")
        break
print("total =", total, "dataLen =", len(data), "loopIters =", guard)


