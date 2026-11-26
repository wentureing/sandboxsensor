package com.sandbox.probe;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 探针项的收集出口：统一套件过滤、skipped 记账与 JSON 组装。
 *
 * 说明：docs/代码镜像/probe/ProbeSink.md
 */
final class ProbeSink {

    /** 本轮收集到的条目（报告里的 entries 数组）。 */
    final JSONArray entries = new JSONArray();

    private final ProbeSuite suite;

    ProbeSink(ProbeSuite suite) {
        this.suite = suite;
    }

    /** 注册一项；本轮 suite 不覆盖时自动记 skipped（代码保留）。 */
    void put(String id, String group, String name, Report.Probe probe) {
        if (!suite.enabled(id)) {
            putSkipped(id, group, name);
            return;
        }
        Report.put(entries, id, group, name, probe);
    }

    /** 显式记一项 skipped（未采样、超时等非失败情形）。 */
    void putSkipped(String id, String group, String name) {
        try {
            JSONObject o = new JSONObject();
            o.put("id", id);
            o.put("group", group);
            o.put("name", name);
            o.put("status", "skipped");
            entries.put(o);
        } catch (Throwable ignored) {
            // 记录 skipped 失败不影响其它项
        }
    }
}
