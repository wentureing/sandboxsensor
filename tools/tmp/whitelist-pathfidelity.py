import io

P = "_ref/ShitJumpingVM/core/model/src/main/java/com/nextvm/core/model/ProcessSlot.kt"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
nl = "\r\n" if "\r\n" in raw else "\n"
lines = raw.split(nl)

assert "PATH_FIDELITY_WHITELIST" not in raw, "已应用"

# 定位 isPathFidelityGuest 那一行
idx = None
for i, l in enumerate(lines):
    if "fun isPathFidelityGuest(packageName: String): Boolean" in l:
        idx = i
        break
assert idx is not None, "isPathFidelityGuest 未找到"

# 它后面那一行是函数体
body_idx = idx + 1
assert "packageName == CM_PACKAGE" in lines[body_idx], f"函数体不符: {lines[body_idx]}"

NEW = '''    /**
     * 走 pathFidelity 的包名白名单 —— **可编辑**。
     *
     * 在白名单里 = 该 guest 的 data 目录走"合成路径"（看起来像真机安装，
     * 实际 I/O 由 v135 规则映射回沙箱）：
     *   getFilesDir()  -> /data/user/0/<pkg>/files        （假路径，实际不存在）
     *   getDataDir()   -> /data/user/0/<pkg>
     *   NativeHookBridge 把 /data/user/0/<pkg>/ 映射回 <sandbox>/
     *
     * 不在白名单 = 直接用沙箱路径（会暴露 <hostPkg>/files/virtual/...）。
     *
     * 已知缺陷（重要）：合成路径只含包名、**不含 instanceId**。
     * 同一个 app 装两份时，两个实例的合成路径相同 -> 规则表是
     * ConcurrentHashMap<from, to>，后注册的覆盖前者 -> 先注册的实例所有 I/O
     * 跑到另一个实例的沙箱（数据损坏级）。
     * **当前只支持单实例**。多实例需要改成 /data/user/<10+slot>/<pkg> 之类的按槽位映射。
     *
     * 已知缺口：external 目录（getExternalFilesDir/getObbDir/getExternalMediaDirs）
     * **没有** pathFidelity 分支，仍返回 <sandbox>/sdcard/Android/data/<pkg>/...，
     * 所以即使在本白名单里，外部存储仍然暴露沙箱。
     */
    private val PATH_FIDELITY_WHITELIST: Set<String> = setOf(
        // 原始定向对抗目标（com.greenpoint.android.mc10086.activity）
        CM_PACKAGE,
        // via：验证 file:// 与 WebView MIME 行为（原先 pathFidelity 只对 CM_PACKAGE 开）
        "mark.via",
    )

    /** 是否对 packageName 启用 pathFidelity。加包名到上面的白名单即可启用。 */
    fun isPathFidelityGuest(packageName: String): Boolean =
        packageName in PATH_FIDELITY_WHITELIST
'''.split("\n")

lines[idx:body_idx + 1] = NEW

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(nl.join(lines))
print("白名单已改为可编辑集合，并写入单实例缺陷与 external 缺口")
