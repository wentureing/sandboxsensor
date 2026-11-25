import io

# ---------- 1) 删除 PathSpoofConfig.load 之后紧邻的 super.onCreate() ----------
P1 = "_ref/ShitJumpingVM/app/src/main/java/com/nextvm/app/NextVmApplication.kt"
with io.open(P1, "r", encoding="utf-8", newline="") as f:
    s = f.read()
nl = "\r\n" if "\r\n" in s else "\n"
lines = s.split(nl)

li = None
for i, l in enumerate(lines):
    if "PathSpoofConfig.load(filesDir)" in l:
        li = i
        break
assert li is not None, "load 行未找到"

# 看后面 1~2 行，删掉第一个纯 super.onCreate()
removed = False
for j in range(li + 1, min(li + 3, len(lines))):
    if lines[j].strip() == "super.onCreate()":
        lines[j] = None
        removed = True
        break
assert removed, "未找到紧随其后的 super.onCreate()"

lines = [l for l in lines if l is not None]
with io.open(P1, "w", encoding="utf-8", newline="") as f:
    f.write(nl.join(lines))
print("NextVmApplication: 删除重复的 super.onCreate()")

# ---------- 2) CmdReceiver 加三条命令 ----------
P2 = "_ref/ShitJumpingVM/app/src/main/java/com/nextvm/app/CmdReceiver.kt"
with io.open(P2, "r", encoding="utf-8", newline="") as f:
    s2 = f.read()
nl2 = "\r\n" if "\r\n" in s2 else "\n"

if "ACTION_SPOOF_EXEMPT_ADD" in s2:
    print("CmdReceiver: 已应用")
    raise SystemExit(0)

anchor = '            else -> Log.w(TAG, "unknown action: $action")'
assert anchor in s2, "else 分支未找到"

NEW_CASES = '''            ACTION_SPOOF_EXEMPT_ADD -> {
                val pkg = intent.getStringExtra("pkg")
                if (pkg.isNullOrBlank()) {
                    Log.w(TAG, "SPOOF_EXEMPT_ADD: missing --es pkg")
                    return
                }
                val changed = com.nextvm.core.model.PathSpoofConfig.setGuestExempt(pkg, true)
                Log.i(
                    TAG,
                    "SPOOF_EXEMPT_ADD $pkg changed=$changed " +
                        "list=${com.nextvm.core.model.PathSpoofConfig.exemptList()} " +
                        "（重启容器/guest 后生效）"
                )
            }

            ACTION_SPOOF_EXEMPT_REMOVE -> {
                val pkg = intent.getStringExtra("pkg")
                if (pkg.isNullOrBlank()) {
                    Log.w(TAG, "SPOOF_EXEMPT_REMOVE: missing --es pkg")
                    return
                }
                val changed = com.nextvm.core.model.PathSpoofConfig.setGuestExempt(pkg, false)
                Log.i(
                    TAG,
                    "SPOOF_EXEMPT_REMOVE $pkg changed=$changed " +
                        "list=${com.nextvm.core.model.PathSpoofConfig.exemptList()} " +
                        "（重启容器/guest 后生效）"
                )
            }

            ACTION_SPOOF_EXEMPT_LIST -> {
                val list = com.nextvm.core.model.PathSpoofConfig.exemptList()
                Log.i(
                    TAG,
                    "SPOOF_EXEMPT_LIST count=${list.size} " +
                        "loaded=${com.nextvm.core.model.PathSpoofConfig.isLoaded()}"
                )
                list.forEach { Log.i(TAG, "SPOOF_EXEMPT pkg=$it") }
            }

'''
s2 = s2.replace(anchor, NEW_CASES + anchor, 1)

anchor2 = '        const val ACTION_CAT_OUT = "com.nextvm.cmd.CAT_OUT"'
assert anchor2 in s2, "常量锚点未找到"
s2 = s2.replace(anchor2, anchor2 + '''

        // --- 路径欺骗豁免（2026-11-25）---
        // 豁免 = 不做任何路径形态欺骗（不产生合成路径），但仍隔离到沙箱
        // 注意：改完需重启容器/guest 才生效
        const val ACTION_SPOOF_EXEMPT_ADD = "com.nextvm.cmd.SPOOF_EXEMPT_ADD"
        const val ACTION_SPOOF_EXEMPT_REMOVE = "com.nextvm.cmd.SPOOF_EXEMPT_REMOVE"
        const val ACTION_SPOOF_EXEMPT_LIST = "com.nextvm.cmd.SPOOF_EXEMPT_LIST"''', 1)

anchor3 = " *   # 列出已安装的 guest（结果打到 logcat -s CmdReceiver）"
assert anchor3 in s2, "注释锚点未找到"
s2 = s2.replace(anchor3, ''' *   # 路径欺骗豁免：这些包不做任何路径形态欺骗（不产生合成路径），但仍隔离到沙箱
 *   # 改完需重启容器/guest 生效
 *   adb shell am broadcast -n com.nextvm.app/.CmdReceiver \\
 *       -a com.nextvm.cmd.SPOOF_EXEMPT_ADD --es pkg mark.via
 *   adb shell am broadcast -n com.nextvm.app/.CmdReceiver \\
 *       -a com.nextvm.cmd.SPOOF_EXEMPT_REMOVE --es pkg mark.via
 *   adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.SPOOF_EXEMPT_LIST
 *
''' + anchor3, 1)

with io.open(P2, "w", encoding="utf-8", newline="") as f:
    f.write(s2)
print("CmdReceiver: 已加三条命令")
