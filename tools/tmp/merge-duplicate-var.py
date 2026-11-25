import io
import re

P = "_ref/ShitJumpingVM/core/hook/src/main/java/com/nextvm/core/hook/NativeHookBridge.kt"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
raw = raw.replace("\r\n", "\n").replace("\r", "\n")

assert "guestExternalDir" in raw, "已应用或无此变量"

# 1) 删掉重复定义行（含前导空白）
before = raw.count("val guestExternalDir = ")
raw = re.sub(r"\n[ \t]*val guestExternalDir = \"\$sandboxDataDir/sdcard/Android/data/\$guestPackageName/\"", "", raw, count=1)
after = raw.count("val guestExternalDir = ")
assert before == 1 and after == 0, f"定义行删除失败: {before} -> {after}"

# 2) 其余引用改为 guestExternalData（同一个值，L428 处已定义，同函数内可见）
n = raw.count("guestExternalDir")
raw = raw.replace("guestExternalDir", "guestExternalData")

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(raw)
print(f"已删除重复定义，{n} 处引用改为 guestExternalData")
