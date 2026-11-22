# 真正的根因：容器的 native hook 污染了 GPU 驱动库

- 发现日期：2026-11-22 18:39
- 推翻的结论：之前把崩溃归因于「Adreno 的 `libllvm-glnext` 编译器缺陷」——**那是症状，不是病因**
- 证据日志：`probe-reports/p12-test-*.txt`（8.74 MB / 66,206 行，31 次崩溃）

---

## 1. 决定性证据

崩溃前 4 条日志（pid 13675，紧接 RenderThread SIGABRT）：

```
D NextVM-Native: Hooked stat in /vendor/lib64/egl/libGLESv2_adreno.so (RELA)
D NextVM-Native: Hooked __system_property_get in /vendor/lib64/egl/libGLESv2_adreno.so (RELA)
D NextVM-Native: Hooked fopen in /vendor/lib64/egl/libGLESv1_CM_adreno.so (RELA)
D NextVM-Native: Hooked __system_property_get in /vendor/lib64/egl/eglSubDriverAndroid.so (RELA)
```

崩溃栈：

```
#00 libc abort
#01-#05 libllvm-glnext.so
#06-#07 libGLESv2_adreno.so          ← shader 编译
#08 GrGLCompileAndAttachShader
#14 TextureOpImpl::onExecute
```

## 2. 因果链

```
容器的 dlopen hook（native-hook.cpp:1037 / 2045）
  → 每次有新库加载，就 patch 它的 GOT/PLT
  → 包括 /vendor/lib64/egl/libGLESv2_adreno.so、eglSubDriverAndroid.so
  → 把这些库里的 stat / fopen / __system_property_get 换成容器的路径重定向实现
  → GPU 驱动在编译 shader 时调用 stat（找 shader 缓存）、fopen（读缓存）、
    __system_property_get（查 GPU 属性）
  → 拿到被重定向/伪造的结果 → 编译器内部状态异常 → abort()
```

**这解释了三件此前无法解释的事**：

| 现象 | 之前的解释 | 真实原因 |
|---|---|---|
| "每个新 shader 变体"都崩 | 驱动缺陷 | 每个新变体都会触发一次驱动的缓存/属性查找 → 进入被污染的 hook |
| `skiagl` / `skiavk` / `opengl` 都崩 | 共用编译器 | 共用的是被污染的驱动库 |
| 软件渲染 `LAYER_TYPE_SOFTWARE` 也崩 | 未解释 | **软件层只影响 Canvas，RenderThread 仍走 GL**，且驱动已被污染 |

## 3. 为什么之前误判

在 `docs/records/render-engine-investigation-20261122.md` 里，我基于：
- 崩溃栈顶部是 `libllvm-glnext`
- Flutter / Chromium / horizon 都有 Adreno Vulkan 的 denylist

得出"这是驱动缺陷"的结论。**错在只看了栈顶，没看崩溃前 4 行的应用日志。** 这是本次排查最大的方法论教训：

> **崩溃栈告诉你"死在哪"，日志才告诉你"为什么死"。栈顶在驱动里，不代表病因在驱动里。**

## 4. 修复

`core/hook/src/main/cpp/native-hook.cpp`：在 patch 每个库之前加路径白名单。

```cpp
if (lib_name != nullptr) {
    const bool is_app_lib =
        strstr(lib_name, "/data/app/") != nullptr ||
        strstr(lib_name, "/data/user/") != nullptr ||
        strstr(lib_name, "/virtual/") != nullptr;
    const bool is_sys_lib =
        strstr(lib_name, "/vendor/") != nullptr ||
        strstr(lib_name, "/system/") != nullptr ||
        strstr(lib_name, "/apex/") != nullptr;
    if (is_sys_lib || !is_app_lib) {
        LOGI("GOT patch skipped (system/vendor lib): %s", lib_name);
        return;
    }
}
```

同时**撤掉**了 `MainActivity` 的 `LAYER_TYPE_SOFTWARE`（无效且让 UI 变慢）。

## 5. 理论依据

目标应用的路径访问最终走的是**它自己库里那份 GOT**。只要 patch 应用库（沙箱里的 `.so`、`/data/app` 下的 APK 内的 `.so`），路径重定向依然生效；**完全没有必要 patch 系统/vendor 库**。

这与 NestSpace 的做法一致（只匹配"当前实例 native library 目录与实例数据目录"的模块）。

## 6. 潜在影响（需要验证）

| 风险 | 说明 |
| --- | --- |
| 路径重定向覆盖率下降 | 若目标应用通过系统库间接访问路径（如某些 libandroid 封装），不再被拦截 |
| `__system_property_get` 伪造失效 | 只对应用库生效；但系统属性伪造本身属于 P2（收益低） |
| 其他系统库不再被 hook | 可能影响某些依赖 hook 系统库的功能（暂无已知用例） |

预期收益远大于风险：**整类 RenderThread SIGABRT 应该消失**。

## 7. 待验证

`p13` 构建后：
1. 启动容器 10 次 → 期望 0 崩溃
2. 点底部三栏、滚动、进 guest（初始版没有 p1/p5/p7 的 UI 修复，圆环和阴影都回来了，但如果 hook 修复生效，这些都不该再崩）
3. 若仍有崩溃，**先看崩溃前 5 行的应用日志**，不要再只看栈顶
