import io

P = "_ref/ShitJumpingVM/core/hook/src/test/java/com/nextvm/core/hook/NativeHookBridgePathTest.kt"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
raw = raw.replace("\r\n", "\n").replace("\r", "\n")
lines = raw.split("\n")

if "T39" in raw:
    print("已应用")
    raise SystemExit(0)

# 去掉文件末尾的 }，再追加
for i in range(len(lines) - 1, -1, -1):
    if lines[i].strip() == "}":
        lines[i] = None
        break
lines = [l for l in lines if l is not None]

NEW = '''
    // ---------------- T39-T41 pathFidelity 的「只误导、不危害读写」契约 ----------------

    /**
     * T39：**每个合成路径都必须被某条规则覆盖**。
     *
     * pathFidelity 的全部价值在于「路径看起来像真机、I/O 仍然正常工作」。
     * 如果某个 synthetic* 路径没有对应规则，guest 拿到假路径后做读写会直接失败
     * —— 那不是「误导」，是「弄坏」。这条测试穷举所有合成路径，逐个验证会被翻译。
     */
    @Test
    fun `T39 每个合成路径都有对应规则`() {
        val apk = "/apk/mark.via/base.apk"
        bridge.setupPathFidelityRedirections(guest, "i1", sandbox, apk)

        val dataRoot = VirtualConstants.syntheticDataDir(guest)
        val extRoot = VirtualConstants.syntheticExternalDir(guest)
        val cases = listOf(
            dataRoot to "$sandbox",
            "$dataRoot/files" to "$sandbox/files",
            "$dataRoot/files/a.txt" to "$sandbox/files/a.txt",
            "$dataRoot/cache" to "$sandbox/cache",
            "$dataRoot/code_cache" to "$sandbox/code_cache",
            "$dataRoot/no_backup" to "$sandbox/no_backup",
            "$dataRoot/databases/x.db" to "$sandbox/databases/x.db",
            "$dataRoot/app_foo" to "$sandbox/app_foo",
            extRoot to "$sandbox/sdcard/Android/data/$guest",
            "$extRoot/files" to "$sandbox/sdcard/Android/data/$guest/files",
            VirtualConstants.syntheticExternalCacheDir(guest)
                to "$sandbox/sdcard/Android/data/$guest/cache",
            VirtualConstants.syntheticObbDir(guest)
                to "$sandbox/sdcard/Android/obb/$guest",
            VirtualConstants.syntheticApkPath(guest) to apk,
            VirtualConstants.syntheticLibDir(guest) + "/libfoo.so"
                to "$sandbox/lib/libfoo.so",
        )
        for ((synth, expect) in cases) {
            val got = bridge.translatePath(synth)
            println("[T39] $synth\\n        -> $got")
            assertNotEquals("合成路径未被任何规则覆盖（会导致读写失败）: $synth", synth, got)
            assertNotEquals("合成路径被映射到 /dev/null: $synth", "/dev/null", got)
            assertEquals("合成路径 $synth", expect, got)
        }
    }

    /**
     * T40：**合成路径必须幂等** —— 已经是沙箱路径的不能再被翻译一次。
     *
     * 这条防的是「规则目标本身又命中另一条规则」导致的二次映射（会把文件搬到别处）。
     */
    @Test
    fun `T40 合成路径翻译幂等`() {
        bridge.setupPathFidelityRedirections(guest, "i1", sandbox, "/apk/x.apk")
        val synth = VirtualConstants.syntheticExternalDir(guest) + "/files/offline/a.mht"
        val once = bridge.translatePath(synth)
        assertEquals(once, bridge.translatePath(once))
        // 沙箱侧不应再被任何规则命中
        assertTrue("翻译结果不在沙箱内: $once", once.startsWith(sandbox))
    }

    /**
     * T41：**合成路径与它的"沙箱真身"必须一一对应，不允许两个合成路径指向同一处**。
     *
     * 反例（真实发生过）：external_data/ 与 sdcard/Android/data/<pkg>/ 都被当成
     * 「external data 的真身」，结果一个被写、另一个被读，列表永远空。
     */
    @Test
    fun `T41 合成路径与沙箱真身一一对应`() {
        bridge.setupPathFidelityRedirections(guest, "i1", sandbox, "/apk/x.apk")
        val synthRoots = listOf(
            VirtualConstants.syntheticDataDir(guest),
            VirtualConstants.syntheticExternalDir(guest),
            VirtualConstants.syntheticObbDir(guest),
            VirtualConstants.syntheticApkPath(guest),
            VirtualConstants.syntheticLibDir(guest),
        )
        val targets = synthRoots.map { bridge.translatePath(it) }
        println("[T41] 合成根 -> 沙箱真身")
        synthRoots.forEachIndexed { i, s -> println("        $s\\n          -> ${targets[i]}") }
        assertEquals("有合成路径映射到了同一个沙箱目标: $targets", targets.size, targets.toSet().size)
        // 且所有目标都必须在沙箱内
        for (t in targets) {
            assertTrue("目标不在沙箱内: $t", t.startsWith(sandbox))
        }
    }
}'''.split("\n")

lines.extend(NEW)

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write("\n".join(lines))
print("已追加 T39-T41")
