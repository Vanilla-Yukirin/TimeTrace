# 采集参数推荐值

> 返回 [Wiki 首页](../readme.md)

---

## 参数说明与推荐值

| 参数 | 推荐值 | 作用 | 说明 |
|------|--------|------|------|
| `min_capture_interval_s` | **2–3 秒** | 防抖下限 | 高频切窗场景下不产生爆炸性截图；低于此值的切换被跳过 |
| `max_capture_interval_s` | **20–60 秒** | 补帧上限 | 长时间停留在同一窗口时仍定期产生关键帧，保证可回放 |
| `idle_threshold_s` | **180–300 秒**（3–5 分钟） | idle 判定 | 无键鼠活动超过此阈值则记录 idle_start 事件，停止补帧 |
| `capture_mode` | `active_window`（默认） | 截图范围 | `active_window` 截活跃窗口；`fullscreen` 截全屏（更大 I/O） |

---

## 当前默认值

`src/timetrace/config.py`：

```python
@dataclass
class CaptureConfig:
    min_capture_interval_s: float = 2.0
    max_capture_interval_s: float = 30.0
    idle_threshold_s: float = 180.0
    capture_mode: str = "active_window"
```

---

## 参数调优建议

### 低功耗场景（笔记本省电模式）
```
min_capture_interval_s = 5.0
max_capture_interval_s = 60.0
idle_threshold_s = 180.0
```

### 高精度记录（工作日志详细模式）
```
min_capture_interval_s = 2.0
max_capture_interval_s = 20.0
idle_threshold_s = 300.0
```

---

## 性能影响

| 参数 | 影响 | 方向 |
|------|------|------|
| `min_capture_interval_s` 越小 | CPU / I/O 越高 | 按需调高 |
| `max_capture_interval_s` 越大 | 时间轴空缺越多 | 按需调低 |
| `idle_threshold_s` 越小 | idle 段检测越灵敏 | 按需调高（避免误判） |

截图写盘是主要 I/O 开销；缩略图生成约消耗原图写入时间的 20–30%。

---

## 相关文档

- [Capture Service（使用这些参数）](../architecture/capture-service.md)
- [性能与稳定性验收标准](testing.md)
