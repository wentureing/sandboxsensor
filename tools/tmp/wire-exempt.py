import io

# ---------- 1) ProcessSlot.kt: isPathFidelityGuest 接入豁免 ----------
P1 = "_ref/ShitJumpingVM/core/model/src/main/java/com/nextvm/core/model/ProcessSlot.kt"
with io.open(P1, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
nl = "\r\n" if "\r\n" in raw else "\n"

OLD = """    /** 是否对 packageName 启用 pathFidelity。加包名到上面的白名单即可启用。 */
    fun isPathFidelityGuest(packageName: String): Boolean =
        packageName in PATH_FIDELITY_WHITELIST"""

NEW = """    /**
     * 是否对 packageName 启用 pathFidelity（= 是否产生合成路径）。
     *
     * **豁免优先**：PathSpoofConfig 里的豁免名单优先级最高，命中则一律返回 false，
     * 不产生任何合成路径（但仍隔离到沙箱，规则由 NativeHookBridge 照常注册）。
     */
    fun isPathFidelityGuest(packageName: String): Boolean {
        if (PathSpoofConfig.isGuestExempt(packageName)) return false
        return packageName in PATH_FIDELITY_WHITELIST
    }"""

if OLD in raw:
    raw = raw.replace(OLD, NEW, 1)
    with io.open(P1, "w", encoding="utf-8", newline="") as f:
        f.write(raw)
    print("ProcessSlot: isPathFidelityGuest 已接入豁免")
elif "PathSpoofConfig.isGuestExempt(packageName)" in raw:
    print("ProcessSlot: 已应用")
else:
    print("ProcessSlot: **未匹配，需人工检查**")

# ---------- 2) NextVmApplication.kt: 启动时 load() ----------
P2 = "_ref/ShitJumpingVM/app/src/main/java/com/nextvm/app/NextVmApplication.kt"
with io.open(P2, "r", encoding="utf-8", newline="") as f:
    raw2 = f.read()
nl2 = "\r\n" if "\r\n" in raw2 else "\n"

if "PathSpoofConfig" in raw2:
    print("NextVmApplication: 已应用")
else:
    # 找 onCreate
    import re
    m = re.search(r"override fun onCreate\(\)\s*\{", raw2)
    assert m, "onCreate 未找到"
    ins = ("override fun onCreate() {\n"
           "        super.onCreate()\n"
           "        // 路径欺骗配置只在启动时读一次（改配置需重启生效）\n"
           "        runCatching { com.nextvm.core.model.PathSpoofConfig.load(filesDir) }\n")
    raw2 = raw2[:m.start()] + ins + raw2[m.end():]
    # 如果原本第一行就是 super.onCreate()，避免重复
    raw2 = raw2.replace("        super.onCreate()\n        // 路径欺骗配置只在启动时读一次（改配置需重启生效）\n        runCatching { com.nextvm.core.model.PathSpoofConfig.load(filesDir) }\n        super.onCreate()",
                        "        super.onCreate()\n        // 路径欺骗配置只在启动时读一次（改配置需重启生效）\n        runCatching { com.nextvm.core.model.PathSpoofConfig.load(filesDir) }")
    with io.open(P2, "w", encoding="utf-8", newline="") as f:
        f.write(raw2)
    print("NextVmApplication: 已加 PathSpoofConfig.load(filesDir)")
