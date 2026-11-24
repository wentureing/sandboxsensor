# 用命名结构关系分析 NEXTVM 与 VirtualApp 的关系

- 日期：2026-11-22
- 方法：**结构映射**（角色对角色、职责对职责），不做字符匹配
- 不关注项：AOSP 移植代码（`ApkLiteParseUtils` 等）按要求排除

---

## 一、为什么结构分析比字符匹配可靠

| 方法 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| 字符匹配（`grep com.lody.virtual`） | 有没有**复制粘贴**的痕迹 | 有没有**概念/架构继承** |
| **结构映射（本报告）** | **哪些角色被继承、哪些被重构、哪些是新增** | 逐行代码相似度 |

举例：一个项目可能把 VA 的 `VActivityManagerService` 改名成 `VirtualActivityManagerService`，字符全不匹配，但**结构和职责完全一样**。反过来，也可能只是撞名。所以必须看**角色和职责**。

---

## 二、结构映射表

### 2.1 同构（角色 + 职责都对应）

| 我们 | VirtualApp | 关系 |
| --- | --- | --- |
| `VirtualActivityManagerService`<br>（`core/services/am/`） | `VActivityManagerService` | **同构**：Activity/进程生命周期 |
| `VirtualPackageManagerService`<br>（`core/services/pm/`） | `VPackageManagerService` | **同构**：包注册与 Intent 解析 |
| `VirtualAccountManager`<br>（`core/services/gms/`） | `VAccountManager` | **同构**：账户体系 |

**命名规律**：`V` → `Virtual` 展开。三个服务**一对一对应**。

### 2.2 同角色、不同实现

| 我们 | VirtualApp | 差异 |
| --- | --- | --- |
| **`VirtualEngine`**（2,050 行，Hilt 单例） | **`VirtualCore`**（全局单例 + **IPC 服务端入口**） | **关键**：我们的 Engine **没有 IPC 服务端**，是进程内直接调用 |
| `SystemServiceProxyManager` + `BinderProxyManager` + 各 `*Proxy` | `ServiceFetcher` + `VResolver` | 我们**按代理类型分模块**，VA 用统一 `ServiceFetcher` 缓存 |
| `ActivityManagerProxy` / `PackageManagerProxy` | `VActivityManager` / `VPackageManager` 的代理层 | 同角色 |

### 2.3 我们有、VA 没有（新增）

| 我们 | 作用 | 为什么 VA 不需要 |
| --- | --- | --- |
| **`VirtualServiceManager`**（161 行，纯服务容器） | Hilt 注入的 9 个服务聚合器 | VA 由 `VirtualCore` 兼任，没有独立"容器"层 |
| **GMS 体系（8 个类）**<br>`VirtualGmsManager` / `GmsBinderBridge` / `GmsFcmProxy` / `GmsPlayStoreProxy` / `GmsPackageIdentitySpoofer` / `VirtualFirebaseAuthProxy` / `VirtualGoogleSignInManager` | Google 服务兼容 | VA 对 GMS 支持很弱 |
| **`VirtualCallerResolver`** | 解析"这次调用来自哪个 guest" | VA 直接用 `Binder.getCallingUid()` |
| **`DeepLinkResolver`** | 深链解析 | VA 无 |
| **`VirtualServiceLifecycleManager`** / `VirtualBroadcastManager` / `VirtualContentProviderManager` | 组件生命周期细分 | VA 把这些塞在 `VActivityManagerService` / `VAppManagerService` 里 |
| **`AntiDetectionEngine`** / `IdentitySpoofingEngine` | 一等公民的对抗模块 | VA 只有零散的 `DeviceInfo` |

### 2.4 VA 有、我们没有（**最重要的架构差异**）

| VA | 我们 | 说明 |
| --- | --- | --- |
| **`VClient`** | **不存在** | **这是最根本的差异，见 §3** |
| `VUi` / `VirtualCore.setUiCallback` | 无 | 我们不需要（Hilt 直接注入） |
| `VirtualCore`（单例 + `startup()` + 服务定位） | `VirtualEngine` + `VirtualServiceManager` | 拆成"引擎"和"服务容器"两件事 |

---

## 三、最根本的差异：**没有 `VClient`**

### 3.1 VA 的模型：集中式 IPC

```
guest 进程
  └─ VClient.main()                    ← guest 进程入口类
       └─ 建立到 VirtualCore 的 IPC
            └─ 所有虚拟服务（AM/PM/Account）通过 Binder 跨进程调用
```

- `VClient` 是 guest 进程里**唯一的接管点**
- 虚拟服务跑在**容器主进程**，guest 只发请求
- 架构上是**客户端/服务端**

### 3.2 我们的模型：进程内直接调用

```
guest 进程（com.nextvm.app:p0）
  └─ ActivityThreadHook 在同一进程注入
       └─ NextVmInstrumentation 接管生命周期
            └─ 直接调用 Hilt 注入的 VirtualServiceManager 实例
```

- **没有独立 Client 类**
- 虚拟服务就是 guest 进程里的**普通对象**（Hilt 单例）
- 架构上是**同一进程内的模块调用**

### 3.3 这个差异的含义

| 维度 | VA（IPC） | 我们（同进程） |
| --- | --- | --- |
| 服务端进程 | 需要常驻主进程 | 不需要，每个 guest 进程自带一套 |
| 跨进程开销 | 每次调用一次 Binder | **零开销**（直接方法调用） |
| 服务状态共享 | 天然共享（一份） | **每个 guest 进程独立**（这才是我们要的隔离） |
| 实现复杂度 | 高（`VClient` + Binder 协议 + 服务端） | 低（直接调用） |
| 多实例隔离 | 靠服务端按 uid 区分 | **天然隔离**（进程级） |

**结论**：这是**架构选择的分歧**，不是"改名"。NEXTVM 用"每个 guest 进程自带服务实例"换来了零 IPC 开销和天然隔离，代价是**服务状态不共享**（例如跨实例的应用列表查询要各查一遍）。

---

## 四、结构复杂度对比（看"演进"的方向）

| 模块 | 我们的类数 | 观察 |
| --- | ---: | --- |
| `core:services` | **17** | 按领域细分（am/pm/component/gms/network/permission/intent） |
| `core:binder` | **7** | 按代理类型分（AM/PM/ContentResolver/System/BinderProxy/CallerResolver） |
| VA 的 `services` 层 | 约 10 | `VActivityManagerService`、`VPackageManagerService`、`VAccountManager`、`VAppManagerService`、`VUserManager`、`VLocationManager`… |

**我们的 services 层比 VA 多了约 50% 的类**，主要集中在：

- **GMS（8 个）** —— VA 几乎没有
- **组件生命周期细分（3 个）** —— VA 塞在 AM 里
- **网络/权限独立成类** —— VA 混在别处

**方向性判断**：NEXTVM 在 VA 的服务划分基础上**做了更细的领域拆分**，并补上了 VA 缺失的 GMS 支持。

---

## 五、最终结论

| 判断项 | 结论 | 依据 |
| --- | --- | --- |
| **是否 VA 代码派生** | ❌ **否** | 无 `VClient`、无 `VirtualCore`、无 `ServiceFetcher`；进程模型根本不同 |
| **是否受 VA 概念影响** | ✅ **是** | AM/PM/Account 三个核心服务**同构命名**（`V`→`Virtual`）；stub 组件 + 进程槽位思路一致 |
| **演进方向** | **重构 + 扩展** | 把 VA 的"集中式 IPC"改为"进程内直调"；把服务拆得更细；补齐 GMS |
| **独立性评级** | **高** | 架构级差异（无 IPC 服务端）> 命名级相似（3 个类名） |

### 一句话总结

> **NEXTVM 借用了 VirtualApp 的「命名与角色划分」，但重写了「进程模型」和「服务组织方式」。**
> 它不是一个改名版 VA，而是一个**在 VA 概念基础上重新设计进程内虚拟化引擎**的项目。

**对本项目约束的影响**：项目要求排除 VA 家族。从结构关系看，**NEXTVM 属于"概念参考"而非"代码派生"**，约束未被违反——但需要明确记录这个边界（命名参考是事实，代码派生不是）。

---

## 六、这个分析方法本身的产出

结构映射除了回答"来源"，还顺带解释了之前几个实际问题的成因：

| 现象 | 结构上的原因 |
| --- | --- |
| **每个 guest 进程自带一套服务实例** | 没有 `VClient`/IPC → `VirtualServiceManager` 是进程内的 Hilt 单例 |
| **`P13` 宿主包可见** | 没有集中的 PM 服务端做统一过滤，每个 guest 进程里的 `VirtualPackageManagerService` 要各自判断"哪些包该隐藏" |
| **`P03` 同 UID 进程数 = 2** | 进程模型是"容器主进程 + guest 进程"，不是 VA 的"主进程 + 多个 guest 但服务集中" |
| **跨实例查询要各查一遍** | 服务状态不共享（这是选择同进程模型的必然代价） |
