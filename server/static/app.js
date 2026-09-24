let currentView = "daily";
// 每个视图独立的日期状态：切换视图互不影响，日期调整只作用于当前视图
let dailyDate = new Date();
let weeklyDate = new Date();
let statsDate = new Date();
let dailyChart = null;
let trendChart = null;
let weeklyProgressChart = null;
let weeklyWowChart = null;
let stabilityChart = null;
let weeklyGoalChart = null;
let trendResizeObserver = null;
let dailyResizeObserver = null;
let dailyLegendSelected = null;
let weeklyCompareMetric = "effective";
let weeklyGoalMetric = "effective";
let lastTrendData = null;      // 最近一次趋势数据（备用）

const TARGET_HOURS = 7;        // 在岗达标线（小时）：日视图目标卡 + 30 天趋势 markLine

const WEEKDAY_CN = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const WEEK_START_DAY = "monday";
const STATS_HISTORY_DAYS = 183;
const COMMUTE_PROFILE_DAYS = 84;
const LEDGER_WEEK_COUNT = 8;
const WORK_CALENDAR_DESKTOP_WEEKS = 26;
const WORK_CALENDAR_MOBILE_WEEKS = 12;
const WEEK_TIMELINE_COLORS = {
    activeOffDuty: "#4FC6C4",
    activeOnDuty: "#4F82DE",
    onDutyIdle: "#ECB0E1",
    offDutyIdle: "#EEF3FB",
};
const DAILY_FOCUS_STYLE = {
    line: "#4CB963",
    fill: "rgba(76,185,99,0.14)",
};
const CHECKIN_COLORS = {
    clockIn: "#0B5DFF",
    clockOut: "#FF3B30",
};
const RATING_PULSE_COLOR = "#FFE787";
const HEAT_TEXT_COLORS = {
    tooLow: "#FF6978",
    low: "#FFE787",
    good: "#4CB963",
    over: "#7D72C6",
    weekendRest: "#9DB8F0",
    weekendWork: "#3F70C9",
};
const WEEKLY_COMPARE_METRICS = {
    effective: { label: "有效在岗", field: "effective_hours" },
    onduty: { label: "总在岗", field: "on_duty_hours" },
    active: { label: "活跃时间", field: "active_hours" },
};
const WEEKLY_GOAL_METRICS = {
    onduty: { label: "总在岗", field: "on_duty_hours", goal: 40 },
    effective: { label: "有效在岗", field: "effective_hours", goal: 32 },
    active: { label: "活跃时间", field: "active_hours", goal: 40 },
};
const WEEKLY_GOAL_METRIC_ORDER = ["effective", "onduty", "active"];
const WEEKLY_GOAL_LINE_COLOR = "#6F7F9A";   // 与 30 天趋势"在岗标准"虚线一致
const WEEKLY_RATING_LINE_COLOR = "#F4B463";

document.addEventListener("DOMContentLoaded", () => {
    dailyChart = echarts.init(document.getElementById("daily-chart"));
    dailyChart.on("legendselectchanged", (evt) => {
        dailyLegendSelected = { ...evt.selected };
    });
    ensureDailyResizeObserver();
    ensureTrendResizeObserver();
    window.addEventListener("resize", () => {
        if (dailyChart) dailyChart.resize();
        if (trendChart) trendChart.resize();
        if (weeklyProgressChart) weeklyProgressChart.resize();
        if (weeklyWowChart) weeklyWowChart.resize();
        if (stabilityChart) stabilityChart.resize();
        if (weeklyGoalChart) weeklyGoalChart.resize();
    });
    updateWeeklyCompareMetricTabs();
    updateWeeklyGoalMetricTabs();
    updateView();
});

function ensureTrendChart() {
    if (trendChart) return;
    const chartEl = document.getElementById("trend-chart");
    if (!chartEl) return;
    trendChart = echarts.init(chartEl);
}

function ensureWeeklyProgressChart() {
    if (weeklyProgressChart) return;
    const chartEl = document.getElementById("weekly-progress-chart");
    if (!chartEl) return;
    weeklyProgressChart = echarts.init(chartEl);
}

function ensureWeeklyWowChart() {
    if (weeklyWowChart) return;
    const chartEl = document.getElementById("weekly-wow-chart");
    if (!chartEl) return;
    weeklyWowChart = echarts.init(chartEl);
}

function ensureStabilityChart() {
    if (stabilityChart) return;
    const chartEl = document.getElementById("stability-chart");
    if (!chartEl) return;
    stabilityChart = echarts.init(chartEl);
}

function ensureWeeklyGoalChart() {
    if (weeklyGoalChart) return;
    const chartEl = document.getElementById("weekly-goal-chart");
    if (!chartEl) return;
    weeklyGoalChart = echarts.init(chartEl);
}

function ensureTrendResizeObserver() {
    if (trendResizeObserver || typeof ResizeObserver === "undefined") return;
    const container = document.querySelector(".container");
    if (!container) return;
    trendResizeObserver = new ResizeObserver(() => {
        if (trendChart) trendChart.resize();
        if (weeklyProgressChart) weeklyProgressChart.resize();
        if (weeklyWowChart) weeklyWowChart.resize();
        if (stabilityChart) stabilityChart.resize();
        if (weeklyGoalChart) weeklyGoalChart.resize();
    });
    trendResizeObserver.observe(container);
}

function ensureDailyResizeObserver() {
    if (dailyResizeObserver || typeof ResizeObserver === "undefined") return;
    const dailyView = document.getElementById("daily-view");
    if (!dailyView) return;
    dailyResizeObserver = new ResizeObserver(() => {
        if (currentView === "daily" && dailyChart) dailyChart.resize();
    });
    dailyResizeObserver.observe(dailyView);
}

function scheduleDailyResize() {
    if (!dailyChart) return;
    requestAnimationFrame(() => {
        if (dailyChart) dailyChart.resize();
    });
    setTimeout(() => {
        if (dailyChart) dailyChart.resize();
    }, 80);
}

function scheduleStatsResize() {
    requestAnimationFrame(() => {
        if (trendChart) trendChart.resize();
        if (stabilityChart) stabilityChart.resize();
        if (weeklyGoalChart) weeklyGoalChart.resize();
    });
    setTimeout(() => {
        if (trendChart) trendChart.resize();
        if (stabilityChart) stabilityChart.resize();
        if (weeklyGoalChart) weeklyGoalChart.resize();
    }, 80);
}

function scheduleWeeklyResize() {
    requestAnimationFrame(() => {
        if (weeklyProgressChart) weeklyProgressChart.resize();
        if (weeklyWowChart) weeklyWowChart.resize();
    });
    setTimeout(() => {
        if (weeklyProgressChart) weeklyProgressChart.resize();
        if (weeklyWowChart) weeklyWowChart.resize();
    }, 80);
}

function switchView(view) {
    currentView = view;
    document.getElementById("btn-daily").classList.toggle("active", view === "daily");
    document.getElementById("btn-weekly").classList.toggle("active", view === "weekly");
    document.getElementById("btn-stats").classList.toggle("active", view === "stats");

    document.getElementById("daily-view").style.display = view === "daily" ? "" : "none";
    document.getElementById("weekly-view").style.display = view === "weekly" ? "" : "none";
    document.getElementById("stats-view").style.display = view === "stats" ? "" : "none";

    document.querySelector(".date-nav").style.visibility = "visible";
    if (view === "stats") {
        ensureTrendChart();
        ensureStabilityChart();
        ensureWeeklyGoalChart();
        scheduleStatsResize();
    }
    if (view === "weekly") {
        ensureWeeklyProgressChart();
        ensureWeeklyWowChart();
        scheduleWeeklyResize();
    }
    if (view === "daily") {
        scheduleDailyResize();
    }

    updateView();
}

function navigate(delta) {
    if (currentView === "daily") {
        dailyDate.setDate(dailyDate.getDate() + delta);
    } else if (currentView === "weekly") {
        weeklyDate.setDate(weeklyDate.getDate() + delta * 7);
    } else {
        // 统计视图：30 天窗口以 1 天为步长滚动（窗口内容不变，end_date 平移一天）
        statsDate.setDate(statsDate.getDate() + delta);
    }
    updateView();
}

function goToToday() {
    if (currentView === "daily") {
        dailyDate = new Date();
    } else if (currentView === "weekly") {
        weeklyDate = new Date();
    } else {
        statsDate = new Date();
    }
    updateView();
}

async function updateView() {
    if (currentView === "daily") {
        await loadDaily();
    } else if (currentView === "weekly") {
        await loadWeekly();
    } else if (currentView === "stats") {
        await loadStats();
    }
}

function weekStart(date) {
    const dt = new Date(date);
    const dayOffset = WEEK_START_DAY === "monday" ? (dt.getDay() + 6) % 7 : dt.getDay();
    dt.setDate(dt.getDate() - dayOffset);
    dt.setHours(0, 0, 0, 0);
    return dt;
}

function fmtDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
}

function setPeriodTag(tagId, from, to) {
    const el = document.getElementById(tagId);
    if (!el || !from || !to) return;
    const fromStr = from.slice(5); // "MM-DD"
    const toStr = to.slice(5);
    el.textContent = from === to ? `（${fromStr}）` : `（${fromStr} ~ ${toStr}）`;
}

function toLocalDate(date) {
    return date instanceof Date ? new Date(date) : new Date(`${date}T00:00:00`);
}

function addDays(date, days) {
    const dt = toLocalDate(date);
    dt.setDate(dt.getDate() + days);
    return dt;
}

function formatMonthDay(date) {
    const dt = toLocalDate(date);
    return `${String(dt.getMonth() + 1).padStart(2, "0")}/${String(dt.getDate()).padStart(2, "0")}`;
}

function formatWeekRangeLabel(startDate) {
    const start = weekStart(toLocalDate(startDate));
    const end = addDays(start, 6);
    return `${formatMonthDay(start)}-${formatMonthDay(end)}`;
}

function toTs(date, timeStr) {
    if (!timeStr) return new Date(`${date}T00:00:00`).getTime();
    if (timeStr.startsWith("24:")) {
        return new Date(`${date}T23:59:59`).getTime() + 1000;
    }
    return new Date(`${date}T${timeStr}`).getTime();
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = String(str ?? "");
    return div.innerHTML;
}

function isCompactDailyAxis() {
    const chartEl = document.getElementById("daily-chart");
    const width = chartEl ? chartEl.clientWidth : window.innerWidth;
    return width > 0 ? width < 420 : window.innerWidth < 420;
}

function formatDailyAxisLabel(value, compact = false) {
    const dt = new Date(value);
    const hh = String(dt.getHours()).padStart(2, "0");
    const mm = String(dt.getMinutes()).padStart(2, "0");
    return compact ? hh : `${hh}:${mm}`;
}

function normalizeCheckinAction(action) {
    const a = String(action || "").trim().toLowerCase();
    if (["clock_in", "check_in", "checkin", "in", "start", "clockin"].includes(a)) return "clock_in";
    if (["clock_out", "check_out", "checkout", "out", "end", "clockout"].includes(a)) return "clock_out";
    return a;
}

function isClockInAction(action) {
    return normalizeCheckinAction(action) === "clock_in";
}

function checkinEmoji(action, current = false, anomaly = false) {
    if (current) return "🕒";
    const a = normalizeCheckinAction(action);
    if (anomaly || a === "uncertain") return "🌀";
    return a === "clock_in" ? "💼" : "🏠";
}

function checkinText(action, anomaly = false, inferred = false, current = false) {
    if (current) return "进行中（截至当前）";
    const label = isClockInAction(action) ? "上班打卡" : "下班打卡";
    if (anomaly) return `${label}（异常）`;
    if (inferred) return `${label}（推断）`;
    return label;
}

function exportCheckinLabel(mark, labels = {}) {
    if (mark && mark.current) return "进行中（截至当前）";
    const action = normalizeCheckinAction(mark && mark.action);
    const label = labels[action] || labels[String(mark && mark.action || "")] || "MARK";
    if (mark && mark.anomaly) return `${label}（异常）`;
    if (mark && mark.inferred) return `${label}（推断）`;
    return label;
}

// 打卡标记分组：保留 action 供文字提示，异常与进行中状态分别映射到 emoji。
function groupCheckinMarks(rawMarks, date, thresholdMs = 10 * 60 * 1000) {
    const CHECKIN_Y = 1.14;
    const result = [];
    for (const m of rawMarks) {
        const ts = toTs(date, m.time);
        result.push([ts, CHECKIN_Y, m.action, !!m.inferred, !!m.anomaly, !!m.current]);
    }
    return result;
}

function ratingPulseSize(raw) {
    const v = Math.max(1, Math.min(5, Math.round(Number(raw || 0))));
    // 整体较之前缩小约 25%，但 1-5 的区分拉大（非线性梯度）
    if (v === 1) return 6;
    if (v === 2) return 8.5;
    if (v === 3) return 11;
    if (v === 4) return 13.5;
    return 16;
}

function classifyDailySegment(seg) {
    const onDuty = Number(seg.opacity ?? 0) >= 0.99;
    if (seg.display_state === "active" && onDuty) return "activeOnDuty";
    if (seg.display_state === "active" && !onDuty) return "activeOffDuty";
    if (seg.display_state === "idle" && onDuty) return "onDutyIdle";
    if (seg.display_state === "idle" && !onDuty) return "offDutyIdle";
    return "offDutyIdle";
}

function segmentAtMinute(segments, minute) {
    return (segments || []).find((s) => Number(s.start_min || 0) <= minute && minute < Number(s.end_min || 0)) || null;
}

function buildDailyRiverBuckets(date, segments, bucketMinutes = 15) {
    const bucketCount = Math.floor((24 * 60) / bucketMinutes);
    const xData = [];
    const buckets = {
        activeOnDuty: Array(bucketCount).fill(0),
        activeOffDuty: Array(bucketCount).fill(0),
        onDutyIdle: Array(bucketCount).fill(0),
        offDutyIdle: Array(bucketCount).fill(0),
    };

    for (let i = 0; i < bucketCount; i++) {
        const minute = i * bucketMinutes;
        const probe = minute + bucketMinutes / 2;
        const dt = new Date(`${date}T00:00:00`);
        dt.setMinutes(minute, 0, 0);
        xData.push(dt.getTime());

        const seg = segmentAtMinute(segments, probe);
        const key = seg ? classifyDailySegment(seg) : "offDutyIdle";
        buckets[key][i] = 1;
    }

    return { xData, buckets };
}

/**
 * 投入强度 — 方案A（Sigmoid / Smoothstep 跟随）
 * 每次状态目标变化都使用 S 形过渡（慢 -> 快 -> 慢），减少“斜斜的”线性观感。
 * 从 min(9:00, 第一个非离岗桶) 开始统计。
 */
function buildDailyFocusValues(segments, bucketMinutes = 15) {
    const bucketCount = Math.floor((24 * 60) / bucketMinutes);
    const nineAM = Math.floor((9 * 60) / bucketMinutes);

    const bucketStates = [];
    let firstActiveBucket = bucketCount;
    for (let i = 0; i < bucketCount; i++) {
        const minute = i * bucketMinutes;
        const probe = minute + bucketMinutes / 2;
        const seg = segmentAtMinute(segments, probe);
        const st = seg ? classifyDailySegment(seg) : "offDutyIdle";
        bucketStates.push(st);
        if (st !== "offDutyIdle" && i < firstActiveBucket) firstActiveBucket = i;
    }

    const startBucket = Math.min(nineAM, firstActiveBucket);

    const TARGET = {
        activeOnDuty: 100,
        activeOffDuty: 78,
        onDutyIdle: 24,
        offDutyIdle: 0,
    };
    const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
    // quintic smootherstep: 慢区更长、快区更窄，避免“斜坡感”过重。
    const smootherstep = (t) => t * t * t * (t * (t * 6 - 15) + 10);
    // 上升更慢（保证连续在岗活跃 >2h 才触顶），下降相对快但仍保持 S 形。
    const upMin = Math.max(1, Math.round(40 / bucketMinutes));
    const upMax = Math.max(upMin + 1, Math.round(180 / bucketMinutes));
    const downMin = Math.max(1, Math.round(25 / bucketMinutes));
    const downMax = Math.max(downMin + 1, Math.round(120 / bucketMinutes));

    const values = [];
    let current = 0;
    let from = 0;
    let to = 0;
    let progress = 1;
    let transitionBuckets = 1;

    for (let i = 0; i < bucketCount; i++) {
        if (i < startBucket) { values.push(0); continue; }
        const target = TARGET[bucketStates[i]] ?? TARGET.offDutyIdle;

        if (i === startBucket || target !== to) {
            from = current;
            to = target;
            const dist = Math.abs(to - from);
            const rising = to > from;
            if (rising) {
                // 15m 桶下，24->100 常见约 10~11 桶（150~165m），满足“>2h 才触顶”。
                transitionBuckets = clamp(Math.round(upMin + dist / 9), upMin, upMax);
            } else {
                transitionBuckets = clamp(Math.round(downMin + dist / 14), downMin, downMax);
            }
            progress = 0;
        }

        progress = Math.min(1, progress + 1 / transitionBuckets);
        current = from + (to - from) * smootherstep(progress);
        values.push(Number(clamp(current, 0, 100).toFixed(1)));
    }
    return values;
}

async function loadDaily() {
    const date = fmtDate(dailyDate);
    document.getElementById("date-label").textContent = date;
    try {
        const [dailyResp, tableResp] = await Promise.all([
            fetch(`/api/dashboard/daily?date=${date}`),
            fetch(`/api/dashboard/table?to=${date}`)
        ]);
        const dailyData = await dailyResp.json();
        const tableData = await tableResp.json();
        // 缓存最近一次日视图数据，供「保存当日分享图」使用
        window.__lastDailyData = dailyData;
        renderDailyChart(dailyData);
        renderDailyStats(dailyData);
        renderTable(tableData);
        scheduleDailyResize();
    } catch (err) {
        console.error("加载日视图失败", err);
    }
}

function renderDailyChart(data) {
    const date = data.date;
    const timeline = data.timeline_segments || [];
    const { xData, buckets } = buildDailyRiverBuckets(date, timeline, 15);
    const focusValues = buildDailyFocusValues(timeline, 15);
    const marks = groupCheckinMarks(data.checkin_marks || [], date);
    const ratings = (data.ratings || []).map((r) => [toTs(date, r.time), 1.36, r.value]);
    const compactAxis = isCompactDailyAxis();
    const option = {
        animationDuration: 300,
        grid: { left: compactAxis ? 36 : 44, right: 16, top: 34, bottom: compactAxis ? 34 : 26 },
        legend: {
            top: 2,
            itemHeight: 8,
            itemWidth: 10,
            textStyle: { color: "#5F7190", fontSize: 11 },
            selectedMode: "multiple",
            selected: dailyLegendSelected || undefined,
            data: ["在岗活跃", "活跃(离岗)", "在岗摸鱼", "离岗", "投入强度", "打卡脉冲", "状态自评"],
        },
        tooltip: {
            trigger: "item",
            formatter: (p) => {
                const t = new Date(p.value[0]).toTimeString().slice(0, 5);
                if (p.seriesName === "打卡脉冲") {
                    return `${checkinText(p.value[2], !!p.value[4], !!p.value[3], !!p.value[5])}<br/>${t}`;
                }
                if (p.seriesName === "状态自评") return `状态自评 ${p.value[2]} 分<br/>${t}`;
                if (p.seriesName === "投入强度") return `投入强度 ${Math.round(Number(p.value[1] || 0))}%<br/>${t}`;
                return `${p.seriesName}<br/>${t}`;
            },
        },
        xAxis: {
            type: "time",
            min: new Date(`${date}T00:00:00`).getTime(),
            max: new Date(`${date}T23:59:59`).getTime(),
            splitNumber: compactAxis ? 4 : 8,
            axisLabel: {
                formatter: (v) => formatDailyAxisLabel(v, compactAxis),
                color: "#637493",
                hideOverlap: true,
                margin: compactAxis ? 12 : 8,
            },
            splitLine: { lineStyle: { color: "rgba(117,145,198,0.28)" } },
        },
        yAxis: [
            {
                type: "value",
                min: 0,
                max: 1.45,
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { show: false },
                splitLine: { show: false },
            },
            {
                type: "value",
                min: 0,
                max: 100,
                position: "right",
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { show: false },
                splitLine: { show: false },
            },
        ],
        series: [
            {
                name: "离岗",
                type: "line",
                stack: "river",
                step: "middle",
                smooth: false,
                symbol: "none",
                lineStyle: { width: 0 },
                itemStyle: { color: WEEK_TIMELINE_COLORS.offDutyIdle },
                areaStyle: { color: WEEK_TIMELINE_COLORS.offDutyIdle, opacity: 0.52 },
                data: xData.map((x, i) => [x, buckets.offDutyIdle[i]]),
            },
            {
                name: "在岗摸鱼",
                type: "line",
                stack: "river",
                step: "middle",
                smooth: false,
                symbol: "none",
                lineStyle: { width: 0 },
                itemStyle: { color: WEEK_TIMELINE_COLORS.onDutyIdle },
                areaStyle: { color: WEEK_TIMELINE_COLORS.onDutyIdle, opacity: 0.85 },
                data: xData.map((x, i) => [x, buckets.onDutyIdle[i]]),
            },
            {
                name: "活跃(离岗)",
                type: "line",
                stack: "river",
                step: "middle",
                smooth: false,
                symbol: "none",
                lineStyle: { width: 0 },
                itemStyle: { color: WEEK_TIMELINE_COLORS.activeOffDuty },
                areaStyle: { color: WEEK_TIMELINE_COLORS.activeOffDuty, opacity: 0.75 },
                data: xData.map((x, i) => [x, buckets.activeOffDuty[i]]),
            },
            {
                name: "在岗活跃",
                type: "line",
                stack: "river",
                step: "middle",
                smooth: false,
                symbol: "none",
                lineStyle: { width: 1.2, color: WEEK_TIMELINE_COLORS.activeOnDuty },
                itemStyle: { color: WEEK_TIMELINE_COLORS.activeOnDuty },
                areaStyle: { color: WEEK_TIMELINE_COLORS.activeOnDuty, opacity: 0.9 },
                data: xData.map((x, i) => [x, buckets.activeOnDuty[i]]),
            },
            {
                name: "投入强度",
                type: "line",
                yAxisIndex: 1,
                smooth: 0.38,
                symbol: "none",
                z: 4,
                lineStyle: { width: 2.1, color: DAILY_FOCUS_STYLE.line },
                itemStyle: { color: DAILY_FOCUS_STYLE.line },
                areaStyle: { color: DAILY_FOCUS_STYLE.fill },
                data: xData.map((x, i) => [x, focusValues[i]]),
            },
            {
                name: "打卡脉冲",
                type: "scatter",
                data: marks,
                symbol: "circle",
                symbolSize: 9,
                z: 12,
                // 用 emoji 作为脉冲视觉标记，替代菱形
                itemStyle: { color: "rgba(0,0,0,0)", borderColor: "rgba(0,0,0,0)" },
                label: {
                    show: true,
                    formatter: (p) => checkinEmoji(p.value[2], !!p.value[5], !!p.value[4]),
                    fontSize: 11,
                    lineHeight: 11,
                },
            },
            {
                name: "状态自评",
                type: "scatter",
                data: ratings,
                symbol: "circle",
                symbolSize: (v) => ratingPulseSize(v[2]),
                z: 12,
                itemStyle: { color: RATING_PULSE_COLOR, borderColor: "#ffffff", borderWidth: 1.2 },
            },
        ],
    };

    dailyChart.setOption(option, true);
}

function renderDailyStats(data) {
    const onDuty = Number(data.on_duty_minutes || 0) / 60;
    const active = Number(data.active_minutes || 0) / 60;
    const focus = Number(data.focus_ratio || 0);
    const ratings = data.ratings || [];
    const avgRating = ratings.length ? (ratings.reduce((s, r) => s + r.value, 0) / ratings.length).toFixed(1) : "-";

    const onDutyPct = Math.min(100, Math.round(onDuty / TARGET_HOURS * 100));
    const activeRate = onDuty > 0 ? Math.round(active / onDuty * 100) : 0;

    const hasOnDuty = Number(data.on_duty_minutes || 0) > 0;
    const slackHours = ((Number(data.on_duty_minutes || 0) - Number(data.effective_minutes || 0)) / 60).toFixed(1);
    const focusCardContent = hasOnDuty
        ? `<div class="stat-value">${escapeHtml(focus.toFixed(1))}%</div>
           <div class="stat-label">在岗专注占比</div>
           <div class="stat-sub">摸鱼 ${escapeHtml(slackHours)}h</div>`
        : `<div class="stat-value">未出勤</div>
           <div class="stat-label">在岗专注占比</div>
           <div class="stat-sub">快来上班</div>`;

    document.getElementById("daily-stats").innerHTML = `
        <div class="stat-card">
            <div class="stat-value">${escapeHtml(onDuty.toFixed(1))}h</div>
            <div class="stat-label">在岗时长</div>
            <div class="stat-sub">目标 ${TARGET_HOURS}h（${onDutyPct}%）</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${escapeHtml(active.toFixed(1))}h</div>
            <div class="stat-label">活跃时长</div>
            <div class="stat-sub">活跃率 ${activeRate}%</div>
        </div>
        <div class="stat-card">
            ${focusCardContent}
        </div>
        <div class="stat-card stat-card-soft">
            <div class="stat-value">${escapeHtml(avgRating)}</div>
            <div class="stat-label">状态自评均分</div>
            <div class="stat-sub">${ratings.length} 次记录</div>
        </div>
    `;
}

async function loadWeekly() {
    ensureWeeklyProgressChart();
    ensureWeeklyWowChart();
    const ws = weekStart(weeklyDate);
    const we = new Date(ws);
    we.setDate(we.getDate() + 6);
    document.getElementById("date-label").textContent = `${fmtDate(ws)} ~ ${fmtDate(we)}`;
    const weekStartStr = fmtDate(ws);
    const historyEnd = fmtDate(we);
    try {
        const [weeklyResp, historyResp] = await Promise.all([
            fetch(`/api/dashboard/weekly?week_start=${weekStartStr}`),
            fetch(`/api/dashboard/history?days=84&end_date=${historyEnd}`),
        ]);
        const weeklyData = await weeklyResp.json();
        const historyData = await historyResp.json();
        renderWeeklyStats(weeklyData);
        renderWeekRows(weeklyData);
        renderWeeklyComparison(weeklyData, historyData);
        scheduleWeeklyResize();
    } catch (err) {
        console.error("加载周视图失败", err);
    }
}

async function loadStats() {
    ensureTrendChart();
    ensureStabilityChart();
    ensureWeeklyGoalChart();
    // 统计视图以 statsDate 为 30 天窗口的结束日（日期导航可滚动窗口）
    const endStr = fmtDate(statsDate);
    const winStart = new Date(statsDate);
    winStart.setDate(winStart.getDate() - 29);
    document.getElementById("date-label").textContent =
        `${fmtDate(winStart).slice(5)} ~ ${fmtDate(statsDate).slice(5)}`;
    try {
        const [historyResp, statsResp] = await Promise.all([
            fetch(`/api/dashboard/history?days=${STATS_HISTORY_DAYS}&end_date=${endStr}`),
            fetch(`/api/dashboard/stats?days=30&end_date=${endStr}`),
        ]);
        const historyData = await historyResp.json();
        const statsData = await statsResp.json();
        const allDays = historyData.days || [];
        window.__lastStatsHistoryDays = allDays;
        lastTrendData = historyData;
        const funDays = allDays.slice(-30);
        const funHistoryData = { ...historyData, days: funDays, from: funDays[0]?.date || historyData.from };
        try { renderFunStats(funHistoryData, statsData); } catch (err) { console.error("renderFunStats failed", err); }
        try { renderTrend(historyData); } catch (err) { console.error("renderTrend failed", err); }
        try { renderStabilityProfile(historyData); } catch (err) { console.error("renderStabilityProfile failed", err); }
        try { renderWeeklyGoalPanel(allDays); } catch (err) { console.error("renderWeeklyGoalPanel failed", err); }
        try { renderWorkCalendar(allDays); } catch (err) { console.error("renderWorkCalendar failed", err); }
        setPeriodTag("fun-stats-period", funHistoryData.from || "", historyData.to || "");
        const stabilityDays = allDays.slice(-COMMUTE_PROFILE_DAYS).filter((day) => day.first_clock_in || day.last_clock_out);
        if (stabilityDays.length) {
            setPeriodTag("efficiency-period", stabilityDays[0].date, stabilityDays[stabilityDays.length - 1].date);
        } else {
            document.getElementById("efficiency-period").textContent = "（暂无数据）";
        }
        scheduleStatsResize();
    } catch (err) {
        console.error("加载统计视图失败", err);
    }
}

function renderTrend(data) {
    if (!trendChart) return;
    const allDays = data.days || [];
    const days = allDays.slice(-30);
    const seriesData = [];
    const labels = [];
    const end = new Date(data.to);

    function resolveFocusBand(rawFocus) {
        const focus = Number(rawFocus || 0);
        if (focus >= 80) return { label: "高", color: HEAT_TEXT_COLORS.good };
        if (focus >= 50) return { label: "中", color: HEAT_TEXT_COLORS.low };
        return { label: "低", color: HEAT_TEXT_COLORS.tooLow };
    }

    for (let i = 29; i >= 0; i--) {
        const d = new Date(end);
        d.setDate(d.getDate() - i);
        const dStr = fmtDate(d);
        labels.push(dStr.slice(5));
        const found = days.find((x) => x.date === dStr);
        if (found) {
            seriesData.push(found);
        } else {
            seriesData.push({ date: dStr, on_duty_hours: 0, active_hours: 0, effective_hours: 0, focus_ratio: 0 });
        }
    }

    const onDutyBar = seriesData.map((item) => {
        const band = resolveFocusBand(item.focus_ratio);
        return {
            value: Number(item.on_duty_hours || 0),
            itemStyle: { color: band.color },
        };
    });
    const activeLine = seriesData.map((item) => Number(item.active_hours || 0));

    // 纵轴自动范围：同时考虑在岗柱子与活跃曲线（活跃可能超出在岗），保证全部数据
    // 可见；数据不足达标线时上限到达标线，超过时 +1h 呼吸空间。随窗口数据变动。
    const maxOnDuty = Math.max(0, ...seriesData.map((d) => Number(d.on_duty_hours || 0)));
    const maxActive = Math.max(0, ...seriesData.map((d) => Number(d.active_hours || 0)));
    const dataMax = Math.max(maxOnDuty, maxActive);
    const yMax = Math.max(
        Math.ceil(dataMax) + (dataMax > TARGET_HOURS ? 1 : 0),
        TARGET_HOURS
    );

    trendChart.setOption(
        {
            animation: true,
            animationDuration: 300,
            animationDurationUpdate: 300,
            grid: { left: 40, right: 30, top: 40, bottom: 30 },
            legend: { data: ["在岗", "活跃"], textStyle: { color: "#637493" }, top: 0 },
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "line" },
                formatter: (params) => {
                    const first = Array.isArray(params) && params.length ? params[0] : null;
                    const idx = first ? first.dataIndex : -1;
                    const row = idx >= 0 ? seriesData[idx] : null;
                    if (!row) return "";
                    const onDutyHours = Number(row.on_duty_hours || 0);
                    const activeHours = Number(row.active_hours || 0);
                    const focus = Number(row.focus_ratio || 0);
                    const focusBand = resolveFocusBand(focus).label;
                    const onDutyMarker = (params.find((p) => p.seriesName === "在岗") || {}).marker || "";
                    const activeMarker = (params.find((p) => p.seriesName === "活跃") || {}).marker || "";
                    return [
                        `${row.date}`,
                        `${onDutyMarker} 在岗: ${onDutyHours.toFixed(1)}h`,
                        `${activeMarker} 活跃: ${activeHours.toFixed(1)}h`,
                        `专注率: ${focus.toFixed(1)}% (${focusBand})`,
                    ].join("<br/>");
                },
            },
            xAxis: { type: "category", data: labels, axisLabel: { color: "#637493" } },
            yAxis: [{ type: "value", name: "小时", max: yMax, axisLabel: { color: "#637493" } }],
            series: [
                {
                    name: "在岗",
                    type: "bar",
                    data: onDutyBar,
                    barMaxWidth: 18,
                    markLine: {
                        silent: true,
                        symbol: "none",
                        animation: true,
                        animationDurationUpdate: 300,
                        lineStyle: { type: "dashed", color: "#6F7F9A", width: 1 },
                        data: [{ yAxis: TARGET_HOURS, label: { formatter: `在岗标准: ${TARGET_HOURS}h`, position: "insideEndTop", color: "#6F7F9A", fontSize: 10 } }],
                    },
                },
                {
                    name: "活跃",
                    type: "line",
                    data: activeLine,
                    smooth: true,
                    symbol: "circle",
                    symbolSize: 4,
                    itemStyle: { color: WEEK_TIMELINE_COLORS.activeOffDuty },
                    lineStyle: { width: 2 },
                },
            ],
        },
        // merge 模式：切日期时增量更新，柱子/曲线/标准线用同一动画时长平滑过渡
        false
    );
}

function renderFunStats(historyData, statsData) {
    const days = historyData.days || [];
    const container = document.getElementById("fun-stats-container");
    container.innerHTML = "";
    if (days.length === 0) {
        container.innerHTML = "<p>暂无足够数据生成统计</p>";
        return;
    }
    // 效率之王需要过滤掉极短工时日，避免“30分钟满专注”误伤长期稳定高效日。
    const FOCUS_KING_MIN_ON_DUTY_HOURS = 2;
    const validFocusDays = days.filter((d) => Number(d.on_duty_hours || 0) >= FOCUS_KING_MIN_ON_DUTY_HOURS);
    const fallbackFocusDays = days.filter((d) => Number(d.on_duty_hours || 0) > 0);
    const focusPool = validFocusDays.length ? validFocusDays : fallbackFocusDays;
    const bestFocusDay = [...focusPool].sort((a, b) => {
        const focusDiff = Number(b.focus_ratio || 0) - Number(a.focus_ratio || 0);
        if (focusDiff !== 0) return focusDiff;
        const effectiveDiff = Number(b.effective_hours || 0) - Number(a.effective_hours || 0);
        if (effectiveDiff !== 0) return effectiveDiff;
        const onDutyDiff = Number(b.on_duty_hours || 0) - Number(a.on_duty_hours || 0);
        if (onDutyDiff !== 0) return onDutyDiff;
        return String(b.date || "").localeCompare(String(a.date || ""));
    })[0] || null;
    const weekStats = [0, 0, 0, 0, 0, 0, 0].map(() => ({ total: 0, count: 0 }));
    days.forEach(d => {
        const dayIdx = new Date(d.date).getDay();
        weekStats[dayIdx].total += Number(d.effective_hours || 0);
        weekStats[dayIdx].count++;
    });
    const weekdayAverages = weekStats
        .map((s, i) => ({ idx: i, avgHours: s.count > 0 ? s.total / s.count : null }))
        .filter((s) => s.avgHours !== null);
    const bestWeekday = weekdayAverages.reduce((best, cur) => {
        if (!best || cur.avgHours > best.avgHours) return cur;
        return best;
    }, null);
    const worstWeekday = weekdayAverages.reduce((worst, cur) => {
        if (!worst || cur.avgHours < worst.avgHours) return cur;
        return worst;
    }, null);
    const rhythmValue = bestWeekday && worstWeekday
        ? `${WEEKDAY_CN[bestWeekday.idx]} / ${WEEKDAY_CN[worstWeekday.idx]}`
        : "-";
    const rhythmSub = bestWeekday && worstWeekday
        ? `${formatMinutesHHMM(bestWeekday.avgHours * 60)} / ${formatMinutesHHMM(worstWeekday.avgHours * 60)}`
        : "无数据";
    const stats = [
        {
            icon: "🥇",
            label: "效率之王",
            value: bestFocusDay ? `${Number(bestFocusDay.focus_ratio || 0).toFixed(1)}%` : "-",
            sub: bestFocusDay && bestFocusDay.date ? bestFocusDay.date.slice(5) : "无数据",
            desc: "窗口内专注率最高的一天。会排除在岗不足 2 小时的短工时日，避免“30 分钟满专注”误伤长期稳定高效的日子。",
        },
        {
            icon: "⚡",
            label: "专注节拍",
            value: rhythmValue,
            sub: rhythmSub,
            desc: "按星期统计有效工时均值：左侧为最有效率的工作日，右侧为最疲软的工作日，反映你的工作节奏规律。",
        },
        {
            icon: "🌅",
            label: "早起冠军",
            value: statsData.early ? statsData.early.time : "-",
            sub: statsData.early ? statsData.early.date.slice(5) : "无数据",
            desc: "窗口内最早一次上班打卡的时间及日期。",
        },
        {
            icon: "🌙",
            label: "深夜战神",
            value: statsData.late ? statsData.late.time : "-",
            sub: statsData.late ? statsData.late.date.slice(5) : "无数据",
            desc: "窗口内最晚一次下班打卡的时间及日期。",
        },
        {
            icon: "🎯",
            label: "专注巅峰",
            value: statsData.peak ? `${statsData.peak.min} min` : "-",
            sub: statsData.peak ? statsData.peak.date.slice(5) : "单次最长活跃",
            desc: "窗口内单次连续活跃的最长时长（未中断的键鼠使用），展示你的最长专注冲刺。",
        },
        {
            icon: "✨",
            label: "黄金小时",
            value: statsData.golden ? statsData.golden.hour : "-",
            sub: statsData.golden && statsData.golden.basis === "active" ? "活跃最高频时段" : "有效活跃最高频时段",
            desc: "一天中活跃最集中的小时段：按原始活跃计算为“活跃最高频”，按在岗有效计算为“有效活跃最高频”，代表你状态最好的时段。",
        }
    ];
    stats.forEach(s => {
        const card = document.createElement("div");
        card.className = "fun-card";
        card.innerHTML = `<div class="fun-icon">${s.icon}</div><div class="fun-label">${s.label}</div><div class="fun-value">${s.value}</div><div class="fun-sub">${s.sub}</div><div class="fun-tip">${s.desc}</div>`;
        container.appendChild(card);
    });
}

function isWeekend(dateStr) {
    const d = new Date(`${dateStr}T00:00:00`);
    const wd = d.getDay();
    return wd === 0 || wd === 6;
}

function isFutureDate(dateStr) {
    const target = new Date(`${dateStr}T00:00:00`);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return target.getTime() > today.getTime();
}

function heatColor(activeMinutes, dateStr) {
    if (isFutureDate(dateStr)) {
        return HEAT_TEXT_COLORS.weekendRest;
    }
    if (isWeekend(dateStr)) {
        return activeMinutes > 60 ? HEAT_TEXT_COLORS.weekendWork : HEAT_TEXT_COLORS.weekendRest;
    }
    if (activeMinutes < 60) return HEAT_TEXT_COLORS.tooLow;
    if (activeMinutes < 240) return HEAT_TEXT_COLORS.low;
    if (activeMinutes <= 540) return HEAT_TEXT_COLORS.good;
    return HEAT_TEXT_COLORS.over;
}

function weekSegmentColor(seg) {
    const onDuty = Number(seg.opacity ?? 0) >= 0.99;
    if (seg.display_state === "active" && onDuty) return WEEK_TIMELINE_COLORS.activeOnDuty;
    if (seg.display_state === "active" && !onDuty) return WEEK_TIMELINE_COLORS.activeOffDuty;
    if (seg.display_state === "idle" && onDuty) return WEEK_TIMELINE_COLORS.onDutyIdle;
    return null;
}

function weekSegmentLabel(seg) {
    const onDuty = Number(seg.opacity ?? 0) >= 0.99;
    if (seg.display_state === "active" && onDuty) return "在岗";
    if (seg.display_state === "active" && !onDuty) return "活跃(离岗)";
    if (seg.display_state === "idle" && onDuty) return "摸鱼";
    return "";
}

function renderWeeklyStats(data) {
    const days = data.days || [];
    const ratingValues = [];
    for (const d of days) {
        for (const r of d.ratings || []) ratingValues.push(r.value);
    }
    const avgRating = ratingValues.length ? (ratingValues.reduce((a, b) => a + b, 0) / ratingValues.length).toFixed(1) : "-";
    const daysWithData = data.days_with_data || 0;
    const totalHours = Number(data.total_hours || 0);
    const activeHours = Number(data.active_hours || 0);
    const activeOffDutyHours = Number(data.active_off_duty_hours || 0);
    const effectiveHours = Number(data.effective_hours || 0);
    const focusRatio = Number(data.focus_ratio || 0);
    const avgOnDuty = daysWithData > 0 ? (totalHours / daysWithData).toFixed(1) : "-";
    const avgEffective = daysWithData > 0 ? (effectiveHours / daysWithData).toFixed(1) : "-";
    document.getElementById("weekly-stats").innerHTML = `
        <div class="stat-card">
            <div class="stat-value">${escapeHtml(String(totalHours))}h</div>
            <div class="stat-label">总在岗</div>
            <div class="stat-sub">日均 ${avgOnDuty}h · 出勤 ${daysWithData}天</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${escapeHtml(String(activeHours))}h</div>
            <div class="stat-label">总活跃</div>
            <div class="stat-sub">未出勤活跃 ${escapeHtml(String(activeOffDutyHours))}h</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${escapeHtml(String(effectiveHours))}h</div>
            <div class="stat-label">在岗专注 · 占比 ${focusRatio}%</div>
            <div class="stat-sub">日均 ${avgEffective}h</div>
        </div>
        <div class="stat-card stat-card-soft">
            <div class="stat-value">${escapeHtml(avgRating)}</div>
            <div class="stat-label">周均状态自评</div>
            <div class="stat-sub">${ratingValues.length} 次评价</div>
        </div>
    `;
}

function renderWeekRows(data) {
    const rowsEl = document.getElementById("week-rows");
    const days = data.days || [];
    rowsEl.innerHTML = "";
    for (const day of days) {
        const dateObj = new Date(`${day.date}T00:00:00`);
        const weekday = WEEKDAY_CN[dateObj.getDay()];
        const active = Number(day.active_minutes || 0);
        const labelColor = heatColor(active, day.date);
        const row = document.createElement("div");
        row.className = "week-row";
        row.innerHTML = `
            <div class="day-label">
                <span class="day-weekday" style="color:${labelColor}">${escapeHtml(weekday)}</span>
                <span class="day-date">${escapeHtml(day.date.slice(5))}</span>
            </div>
            <div class="timeline-track"></div>
        `;
        const track = row.querySelector(".timeline-track");
        for (const seg of day.timeline_segments || []) {
            const start = Number(seg.start_min || 0), end = Number(seg.end_min || 0);
            if (end <= start) continue;
            const segColor = weekSegmentColor(seg);
            if (!segColor) continue;
            const part = document.createElement("span");
            part.className = "track-segment";
            part.style.left = `${(start / 1440) * 100}%`;
            part.style.width = `${((end - start) / 1440) * 100}%`;
            part.style.background = segColor;
            part.style.opacity = "1";
            part.title = weekSegmentLabel(seg);
            track.appendChild(part);
        }
        for (const mark of day.checkin_marks || []) {
            const mins = toMinutes(mark.time);
            if (mins == null) continue;
            const line = document.createElement("i");
            line.className = "checkin-mark";
            line.style.left = `${(mins / 1440) * 100}%`;
            track.appendChild(line);
        }
        rowsEl.appendChild(row);
    }
}

function toMinutes(timeStr) {
    if (!timeStr) return null;
    const p = String(timeStr).split(":").map(Number);
    if (p.length < 2 || Number.isNaN(p[0]) || Number.isNaN(p[1])) return null;
    return p[0] * 60 + p[1];
}

function formatMinutesHHMM(totalMinutes) {
    const mins = Math.max(0, Math.round(Number(totalMinutes || 0)));
    const hh = String(Math.floor(mins / 60)).padStart(2, "0");
    const mm = String(mins % 60).padStart(2, "0");
    return `${hh}:${mm}`;
}

function getEarliestHistoryDate(days) {
    const valid = (days || []).filter((day) => day && day.date && (Number(day.on_duty_hours || 0) > 0 || Number(day.active_hours || 0) > 0 || Number(day.effective_hours || 0) > 0));
    return valid.length ? valid[0].date : null;
}

function updateWeeklyCompareMetricTabs() {
    const container = document.getElementById("weekly-compare-metric-tabs");
    if (!container) return;
    container.innerHTML = Object.entries(WEEKLY_COMPARE_METRICS)
        .map(([key, meta]) => `<button class="${weeklyCompareMetric === key ? "active" : ""}" data-metric="${key}">${meta.label}</button>`)
        .join("");
    container.querySelectorAll("button").forEach((button) => {
        button.addEventListener("click", () => {
            weeklyCompareMetric = button.dataset.metric;
            updateWeeklyCompareMetricTabs();
            if (window.__lastWeeklyData && window.__lastWeeklyHistory) {
                renderWeeklyComparison(window.__lastWeeklyData, window.__lastWeeklyHistory);
                scheduleWeeklyResize();
            }
        });
    });
}

function buildWeeklyCompareRows(currentWeek, historyDays) {
    const field = WEEKLY_COMPARE_METRICS[weeklyCompareMetric].field;
    const ws = toLocalDate(currentWeek.week_start);
    const previousWeekStart = fmtDate(addDays(ws, -7));
    const historyMap = new Map((historyDays || []).map((day) => [day.date, day]));
    const earliestDate = getEarliestHistoryDate(historyDays || []);
    const earliestTs = earliestDate ? toLocalDate(earliestDate).getTime() : null;
    const weekdayOrder = [1, 2, 3, 4, 5, 6, 0];
    const weekdayRows = [];
    const todayStr = fmtDate(new Date());
    let cumulativeCurrent = 0;
    let cumulativeBaseline = 0;

    for (const weekdayIdx of weekdayOrder) {
        const offset = (weekdayIdx - ws.getDay() + 7) % 7;
        const currentDate = fmtDate(addDays(ws, offset));
        const previousDate = fmtDate(addDays(ws, offset - 7));
        const currentDay = (currentWeek.days || []).find((day) => day.date === currentDate) || {};
        const previousDay = historyMap.get(previousDate) || {};
        const sameWeekdayHistory = (historyDays || []).filter((day) => {
            const ts = toLocalDate(day.date).getTime();
            return new Date(`${day.date}T00:00:00`).getDay() === weekdayIdx
                && fmtDate(weekStart(day.date)) !== currentWeek.week_start
                && fmtDate(weekStart(day.date)) !== previousWeekStart
                && (!earliestTs || ts >= earliestTs);
        }).slice(-4);
        const baselineValue = sameWeekdayHistory.length ? sameWeekdayHistory.reduce((sum, day) => sum + Number(day[field] || 0), 0) / sameWeekdayHistory.length : 0;
        const currentValue = Number(currentDay[field.replace("_hours", "_minutes")] || 0) / 60 || Number(currentDay[field] || 0) || 0;
        const previousValue = Number(previousDay[field] || 0);
        cumulativeCurrent += currentValue;
        cumulativeBaseline += baselineValue;
        weekdayRows.push({
            label: WEEKDAY_CN[weekdayIdx],
            currentValue: Number(currentValue.toFixed(1)),
            previousValue: Number(previousValue.toFixed(1)),
            baselineValue: Number(baselineValue.toFixed(1)),
            currentCumulative: Number(cumulativeCurrent.toFixed(1)),
            baselineCumulative: Number(cumulativeBaseline.toFixed(1)),
            deltaValue: Number((currentValue - previousValue).toFixed(1)),
            isFuture: currentDate > todayStr,
        });
    }
    return weekdayRows;
}

function renderWeeklyComparison(currentWeek, historyData) {
    window.__lastWeeklyData = currentWeek;
    window.__lastWeeklyHistory = historyData;
    const rows = buildWeeklyCompareRows(currentWeek, historyData.days || []);
    renderWeeklyProgress(rows);
    renderWeeklyWow(rows);
}

function renderWeeklyProgress(rows) {
    if (!weeklyProgressChart) return;
    weeklyProgressChart.setOption({
        animationDuration: 300,
        tooltip: { trigger: "axis" },
        legend: { data: ["本周累计", "历史基线累计"], top: 0, textStyle: { color: "#637493" } },
        grid: { left: 42, right: 18, top: 38, bottom: 28 },
        xAxis: { type: "category", data: rows.map((row) => row.label), axisLabel: { color: "#637493" } },
        yAxis: { type: "value", name: "小时", axisLabel: { color: "#637493" } },
        series: [
            {
                name: "本周累计",
                type: "line",
                smooth: true,
                symbolSize: 7,
                lineStyle: { width: 3, color: WEEK_TIMELINE_COLORS.activeOnDuty },
                itemStyle: { color: WEEK_TIMELINE_COLORS.activeOnDuty },
                areaStyle: { color: "rgba(79,130,222,0.08)" },
                data: rows.map((row) => row.currentCumulative),
            },
            {
                name: "历史基线累计",
                type: "line",
                smooth: true,
                symbolSize: 6,
                lineStyle: { width: 2, color: "#A4B1C8", type: "dashed" },
                itemStyle: { color: "#A4B1C8" },
                data: rows.map((row) => row.baselineCumulative),
            },
        ],
    }, true);
}

function renderWeeklyWow(rows) {
    if (!weeklyWowChart) return;
    const currentData = rows.map((row, index) => (row.isFuture ? ["-", index] : [row.currentValue, index]));
    const previousData = rows.map((row, index) => ({
        value: [row.previousValue, index],
        itemStyle: row.isFuture ? { color: "#D8E0EC" } : undefined,
    }));
    weeklyWowChart.setOption({
        animationDuration: 300,
        tooltip: {
            trigger: "item",
            formatter: (params) => {
                const row = rows[params.dataIndex];
                if (row.isFuture) {
                    return `${row.label}<br/>上周 ${row.previousValue.toFixed(1)}h`;
                }
                return `${row.label}<br/>上周 ${row.previousValue.toFixed(1)}h<br/>本周 ${row.currentValue.toFixed(1)}h<br/>环比 ${row.deltaValue >= 0 ? "+" : ""}${row.deltaValue.toFixed(1)}h`;
            },
        },
        grid: { left: 50, right: 28, top: 16, bottom: 20 },
        xAxis: { type: "value", name: "小时", axisLabel: { color: "#637493" } },
        yAxis: {
            type: "category",
            data: rows.map((row) => row.label),
            inverse: true,
            axisLabel: {
                color: "#637493",
                formatter: (value, index) => (rows[index] && rows[index].isFuture ? `{future|${value}}` : value),
                rich: { future: { color: "#B6C2D6" } },
            },
        },
        series: [
            {
                type: "custom",
                renderItem(params, api) {
                    const idx = params.dataIndex;
                    const row = rows[idx];
                    const y = api.coord([0, idx])[1];
                    if (row.isFuture) return;
                    const start = api.coord([row.previousValue, idx])[0];
                    const end = api.coord([row.currentValue, idx])[0];
                    return {
                        type: "line",
                        shape: { x1: start, y1: y, x2: end, y2: y },
                        style: { stroke: row.deltaValue >= 0 ? HEAT_TEXT_COLORS.good : HEAT_TEXT_COLORS.tooLow, lineWidth: 3 },
                    };
                },
                data: rows.map((_, index) => index),
                silent: true,
            },
            {
                name: "上周",
                type: "scatter",
                symbolSize: 10,
                itemStyle: { color: "#A4B1C8" },
                data: previousData,
            },
            {
                name: "本周",
                type: "scatter",
                symbolSize: 12,
                itemStyle: { color: WEEK_TIMELINE_COLORS.activeOnDuty },
                data: currentData,
                label: {
                    show: true,
                    position: "right",
                    color: "#637493",
                    fontSize: 10,
                    formatter: (params) => {
                        const row = rows[params.dataIndex];
                        if (row.isFuture) return "";
                        const delta = row.deltaValue;
                        return `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}h`;
                    },
                },
            },
        ],
    }, true);
}

function percentile(values, q) {
    const sorted = values.map(Number).filter((v) => !Number.isNaN(v)).sort((a, b) => a - b);
    if (!sorted.length) return null;
    if (sorted.length === 1) return sorted[0];
    const pos = (sorted.length - 1) * q;
    const base = Math.floor(pos);
    const rest = pos - base;
    const next = sorted[base + 1];
    return next === undefined ? sorted[base] : sorted[base] + rest * (next - sorted[base]);
}

function buildDistribution(values) {
    if (!values.length) return null;
    return {
        p10: percentile(values, 0.10),
        q1: percentile(values, 0.25),
        median: percentile(values, 0.50),
        q3: percentile(values, 0.75),
        p90: percentile(values, 0.90),
        count: values.length,
    };
}

function buildCommuteProfile(days) {
    const sortedDays = [...(days || []).slice(-COMMUTE_PROFILE_DAYS)].sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const normalized = sortedDays.map((d) => ({
        date: d.date,
        on_duty_hours: Number(d.on_duty_hours || 0),
        active_hours: Number(d.active_hours || 0),
        _firstIn: parseTimeToHour(d.first_clock_in),
        _lastOut: parseTimeToHour(d.last_clock_out),
    }));
    for (let i = normalized.length - 1; i > 0; i--) {
        const cur = normalized[i];
        const prev = normalized[i - 1];
        if (cur._lastOut === null || cur._lastOut >= 6) continue;
        const crossover = cur._firstIn === null || cur._lastOut < cur._firstIn;
        if (!crossover) continue;
        const shifted = cur._lastOut + 24;
        if (prev._lastOut === null || shifted > prev._lastOut) {
            prev._lastOut = shifted;
        }
        cur._lastOut = null;
    }
    return [1, 2, 3, 4, 5, 6, 0].map((weekdayIdx) => {
        const rows = normalized.filter((day) => {
            return new Date(`${day.date}T00:00:00`).getDay() === weekdayIdx
                && (day.on_duty_hours > 0 || day.active_hours > 0);
        });
        const starts = rows.map((day) => day._firstIn).filter((v) => v !== null);
        const ends = rows.map((day) => day._lastOut).filter((v) => v !== null);
        return {
            label: WEEKDAY_CN[weekdayIdx],
            start: buildDistribution(starts),
            end: buildDistribution(ends),
        };
    }).filter((row) => row.start && row.end);
}

function renderStabilityProfile(historyData) {
    if (!stabilityChart) return;
    const profile = buildCommuteProfile(historyData.days || []);
    if (!profile.length) {
        stabilityChart.clear();
        return;
    }
    const yMin = 8;
    const yMax = 26;
    const clampTime = (value) => Math.max(yMin, Math.min(yMax, Number(value)));
    const distributionText = (label, item) => `${label}：${formatClockHour(item.q1)} - ${formatClockHour(item.q3)}，中位 ${formatClockHour(item.median)}，样本 ${item.count}`;
    stabilityChart.setOption({
        animationDuration: 300,
        tooltip: {
            trigger: "item",
            confine: true,
            formatter: (params) => {
                const row = profile[params.dataIndex];
                return `${row.label}<br/>${distributionText("上班", row.start)}<br/>${distributionText("下班", row.end)}`;
            },
        },
        grid: { left: 46, right: 14, top: 14, bottom: 30 },
        xAxis: {
            type: "category",
            data: profile.map((row) => row.label),
            boundaryGap: true,
            axisTick: { show: false },
            axisLine: { lineStyle: { color: "#d9e2ef" } },
            axisLabel: { color: "#637493", fontSize: 12 },
        },
        yAxis: {
            type: "value",
            min: yMin,
            max: yMax,
            interval: 2,
            axisLabel: { color: "#637493", fontSize: 11, formatter: formatClockHour },
            axisLine: { show: true, lineStyle: { color: "#d9e2ef" } },
            splitLine: { lineStyle: { color: "rgba(164,177,200,0.25)" } },
        },
        series: [
            {
                type: "custom",
                data: profile.map((_, index) => index),
                renderItem(params, api) {
                    const row = profile[params.dataIndex];
                    const x = api.coord([params.dataIndex, yMin])[0];
                    const capsule = (item, offset, color, softColor) => {
                        const bandTop = api.coord([params.dataIndex, clampTime(item.p90)])[1];
                        const bandBottom = api.coord([params.dataIndex, clampTime(item.p10)])[1];
                        const coreTop = api.coord([params.dataIndex, clampTime(item.q3)])[1];
                        const coreBottom = api.coord([params.dataIndex, clampTime(item.q1)])[1];
                        const midY = api.coord([params.dataIndex, clampTime(item.median)])[1];
                        return [
                            { type: "rect", shape: { x: x + offset - 9, y: bandTop, width: 18, height: Math.max(4, bandBottom - bandTop), r: 9 }, style: { fill: softColor } },
                            { type: "rect", shape: { x: x + offset - 5, y: coreTop, width: 10, height: Math.max(4, coreBottom - coreTop), r: 5 }, style: { fill: color } },
                            { type: "circle", shape: { cx: x + offset, cy: midY, r: 4.5 }, style: { fill: "#fff", stroke: color, lineWidth: 2.4 } },
                        ];
                    };
                    return {
                        type: "group",
                        children: [
                            ...capsule(row.start, -9, WEEK_TIMELINE_COLORS.activeOnDuty, "rgba(79,130,222,0.20)"),
                            ...capsule(row.end, 9, WEEK_TIMELINE_COLORS.activeOffDuty, "rgba(79,198,196,0.22)"),
                        ],
                    };
                },
            },
        ],
    }, true);
}

function formatSignedHours(value) {
    const n = Number(value || 0);
    return `${n >= 0 ? "+" : ""}${n.toFixed(1)}h`;
}

// 与 30 天趋势"在岗柱"同一染色约定：柱色 = 专注率分档
function weeklyFocusBand(rawFocus) {
    const focus = Number(rawFocus || 0);
    if (focus >= 80) return { label: "高", color: HEAT_TEXT_COLORS.good };
    if (focus >= 50) return { label: "中", color: HEAT_TEXT_COLORS.low };
    return { label: "低", color: HEAT_TEXT_COLORS.tooLow };
}

function updateWeeklyGoalMetricTabs() {
    const container = document.getElementById("weekly-goal-metric-tabs");
    if (!container) return;
    container.innerHTML = WEEKLY_GOAL_METRIC_ORDER
        .map((key) => `<button class="${weeklyGoalMetric === key ? "active" : ""}" data-metric="${key}">${WEEKLY_GOAL_METRICS[key].label}</button>`)
        .join("");
    container.querySelectorAll("button").forEach((button) => {
        button.addEventListener("click", () => {
            weeklyGoalMetric = button.dataset.metric;
            updateWeeklyGoalMetricTabs();
            if (window.__lastStatsHistoryDays) {
                renderWeeklyGoalPanel(window.__lastStatsHistoryDays);
                scheduleStatsResize();
            }
        });
    });
}

function renderWeeklyGoalPanel(days) {
    const kpisEl = document.getElementById("weekly-goal-kpis");
    const periodEl = document.getElementById("weekly-ledger-period");
    if (!kpisEl) return;
    ensureWeeklyGoalChart();
    const weeks = aggregateHistoryByWeek(days, 52)
        .filter((week) => !week.is_partial && Number(week.days_with_data || 0) > 0)
        .slice(-LEDGER_WEEK_COUNT);
    const metric = WEEKLY_GOAL_METRICS[weeklyGoalMetric] || WEEKLY_GOAL_METRICS.effective;
    const goal = metric.goal;
    if (!weeks.length) {
        kpisEl.innerHTML = '<div class="goal-kpi"><small>暂无完整周数据</small><strong>—</strong></div>';
        if (weeklyGoalChart) weeklyGoalChart.clear();
        if (periodEl) periodEl.textContent = "（暂无数据）";
        return;
    }
    const values = weeks.map((week) => Number(week[metric.field] || 0));
    const labels = weeks.map((week) => formatMonthDay(week.week_start));
    const metCount = values.filter((value) => value >= goal).length;
    let streak = 0;
    for (let i = values.length - 1; i >= 0; i--) {
        if (values[i] >= goal) streak++;
        else break;
    }
    const bestIdx = values.indexOf(Math.max(...values));
    const avgDelta = values.reduce((sum, value) => sum + value, 0) / values.length - goal;
    const kpis = [
        { label: `达标周数（≥${goal}h）`, value: `${metCount} / ${weeks.length}`, cls: metCount > 0 ? "goal-good" : "goal-bad" },
        { label: "当前连续达标", value: `${streak} 周`, cls: "" },
        { label: "最佳周", value: `${labels[bestIdx]}（${values[bestIdx].toFixed(1)}h）`, cls: "" },
        { label: `${weeks.length} 周均值 vs 目标`, value: formatSignedHours(avgDelta), cls: avgDelta >= 0 ? "goal-good" : "goal-bad" },
    ];
    kpisEl.innerHTML = kpis
        .map((kpi) => `<div class="goal-kpi"><small>${kpi.label}</small><strong class="${kpi.cls}">${kpi.value}</strong></div>`)
        .join("");

    if (weeklyGoalChart) {
        weeklyGoalChart.setOption({
            animationDuration: 300,
            tooltip: {
                trigger: "axis",
                confine: true,
                formatter: (params) => {
                    const first = Array.isArray(params) && params.length ? params[0] : null;
                    const idx = first ? first.dataIndex : -1;
                    const week = idx >= 0 ? weeks[idx] : null;
                    if (!week) return "";
                    const value = Number(week[metric.field] || 0);
                    const band = weeklyFocusBand(week.focus_ratio);
                    const ratingText = week.rating_avg === null || week.rating_avg === undefined
                        ? "—"
                        : Number(week.rating_avg).toFixed(1);
                    return [
                        `<b>${formatWeekRangeLabel(week.week_start)}</b>`,
                        `${metric.label}：${value.toFixed(1)}h（${formatSignedHours(value - goal)}）`,
                        `专注率：${Number(week.focus_ratio || 0).toFixed(1)}%（${band.label}）`,
                        `平均自评分：${ratingText}`,
                    ].join("<br/>");
                },
            },
            legend: { top: 0, data: ["平均自评分"], textStyle: { color: "#637493", fontSize: 11 }, itemWidth: 14 },
            grid: { left: 40, right: 44, top: 30, bottom: 26 },
            xAxis: {
                type: "category",
                data: labels,
                axisLabel: { color: "#637493", fontSize: 11 },
                axisLine: { lineStyle: { color: "#d9e2ef" } },
            },
            yAxis: [
                {
                    type: "value",
                    max: Math.ceil(Math.max(goal, ...values) * 1.12),
                    axisLabel: { color: "#637493", fontSize: 11 },
                    splitLine: { lineStyle: { color: "rgba(164,177,200,0.25)" } },
                },
                {
                    type: "value",
                    min: 1,
                    max: 5,
                    interval: 1,
                    axisLabel: { color: "#637493", fontSize: 11 },
                    splitLine: { show: false },
                },
            ],
            series: [
                {
                    name: metric.label,
                    type: "bar",
                    data: weeks.map((week, index) => ({
                        value: values[index],
                        itemStyle: { color: weeklyFocusBand(week.focus_ratio).color, borderRadius: [6, 6, 0, 0] },
                    })),
                    barWidth: "52%",
                    label: { show: true, position: "top", fontSize: 10, color: "#637493", formatter: (p) => Number(p.value).toFixed(1) },
                    markLine: {
                        silent: true,
                        symbol: "none",
                        lineStyle: { type: "dashed", color: WEEKLY_GOAL_LINE_COLOR, width: 1 },
                        label: { formatter: `周目标 ${goal}h`, color: WEEKLY_GOAL_LINE_COLOR, fontSize: 10, position: "insideEndTop" },
                        data: [{ yAxis: goal }],
                    },
                },
                {
                    name: "平均自评分",
                    type: "line",
                    yAxisIndex: 1,
                    data: weeks.map((week) => week.rating_avg),
                    connectNulls: true,
                    smooth: 0.3,
                    symbolSize: 7,
                    lineStyle: { width: 2.5, color: WEEKLY_RATING_LINE_COLOR },
                    itemStyle: { color: WEEKLY_RATING_LINE_COLOR },
                },
            ],
        }, true);
    }
    if (periodEl) setPeriodTag("weekly-ledger-period", weeks[0].week_start, weeks[weeks.length - 1].week_end);
}

function workCalendarLevel(day) {
    if (!day) return "empty";
    const active = Number(day.active_hours || 0);
    const effective = Number(day.effective_hours || 0);
    if (active > 0 && effective <= 0) return "zero";
    if (effective <= 0) return "empty";
    if (effective < 1.5) return "l1";
    if (effective < 3) return "l2";
    if (effective < 5) return "l3";
    return "l4";
}

function renderWorkCalendar(days) {
    const dayByDate = new Map((days || []).map((day) => [day.date, day]));
    renderWorkCalendarMatrix("work-calendar-desktop", WORK_CALENDAR_DESKTOP_WEEKS, dayByDate);
    renderWorkCalendarMatrix("work-calendar-mobile", WORK_CALENDAR_MOBILE_WEEKS, dayByDate);
}

function renderWorkCalendarMatrix(targetId, weekCount, dayByDate) {
    const target = document.getElementById(targetId);
    if (!target) return;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const currentWeekStart = weekStart(today);
    const firstWeekStart = addDays(currentWeekStart, -(weekCount - 1) * 7);
    const weekStarts = Array.from({ length: weekCount }, (_, index) => addDays(firstWeekStart, index * 7));
    const monthLabels = weekStarts.map((date, index) => {
        return index === 0 || date.getDate() <= 7 ? `${date.getMonth() + 1}月` : "";
    });
    const weekdayLabels = ["一", "二", "三", "四", "五", "六", "日"];
    let html = `<div class="work-calendar-grid" style="--calendar-weeks:${weekCount}">`;
    html += `<span></span>${monthLabels.map((label) => `<span class="calendar-month">${label}</span>`).join("")}`;
    for (let dow = 0; dow < 7; dow++) {
        html += `<span class="calendar-weekday">${weekdayLabels[dow]}</span>`;
        for (const weekStartDate of weekStarts) {
            const date = addDays(weekStartDate, dow);
            const key = fmtDate(date);
            if (date > today) {
                html += '<span class="calendar-cell calendar-future" aria-hidden="true"></span>';
                continue;
            }
            const day = dayByDate.get(key);
            const level = workCalendarLevel(day);
            const title = day ? `${key} 有效 ${Number(day.effective_hours || 0).toFixed(1)}h / 活跃 ${Number(day.active_hours || 0).toFixed(1)}h` : `${key} 暂无数据`;
            html += `<span class="calendar-cell heat-${level}" title="${title}"></span>`;
        }
    }
    html += "</div>";
    target.innerHTML = html;
}

function parseTimeToHour(timeStr) {
    if (!timeStr) return null;
    const parts = String(timeStr).split(":");
    if (parts.length < 2) return null;
    const h = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10);
    if (isNaN(h) || isNaN(m)) return null;
    return h + m / 60;
}

function formatClockHour(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    const totalMinutes = Math.round(Number(value) * 60);
    const crossesMidnight = totalMinutes >= 24 * 60;
    const normalized = ((totalMinutes % (24 * 60)) + (24 * 60)) % (24 * 60);
    const hour = Math.floor(normalized / 60);
    const minute = normalized % 60;
    const label = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
    return crossesMidnight ? `次日 ${label}` : label;
}

function aggregateHistoryByWeek(days, weekCount = LEDGER_WEEK_COUNT) {
    const buckets = new Map();
    for (const day of days || []) {
        if (!day || !day.date) continue;
        const date = toLocalDate(day.date);
        if (Number.isNaN(date.getTime())) continue;
        const start = weekStart(date);
        const key = fmtDate(start);
        if (!buckets.has(key)) {
            buckets.set(key, {
                week_start: key,
                week_end: fmtDate(addDays(start, 6)),
                on_duty_hours: 0,
                active_hours: 0,
                effective_hours: 0,
                rating_sum: 0,
                rating_count: 0,
                days_with_data: 0,
            });
        }
        const bucket = buckets.get(key);
        const onDutyHours = Number(day.on_duty_hours || 0);
        const activeHours = Number(day.active_hours || 0);
        const effectiveHours = Number(day.effective_hours || 0);
        const ratingAvg = Number(day.rating_avg || 0);
        const ratingCount = Number(day.rating_count || 0);
        bucket.on_duty_hours += onDutyHours;
        bucket.active_hours += activeHours;
        bucket.effective_hours += effectiveHours;
        bucket.rating_sum += ratingAvg * ratingCount;
        bucket.rating_count += ratingCount;
        if (onDutyHours > 0 || activeHours > 0) bucket.days_with_data += 1;
    }

    const currentWeekStartKey = fmtDate(weekStart(new Date()));
    return [...buckets.values()]
        .sort((a, b) => String(a.week_start).localeCompare(String(b.week_start)))
        .slice(-weekCount)
        .map((bucket) => {
            const onDutyHours = Number(bucket.on_duty_hours || 0);
            const activeHours = Number(bucket.active_hours || 0);
            const effectiveHours = Number(bucket.effective_hours || 0);
            const onDutyIdleHours = Math.max(0, onDutyHours - effectiveHours);
            const activeOffDutyHours = Math.max(0, activeHours - effectiveHours);
            const focusRatio = onDutyHours > 0 ? effectiveHours / onDutyHours * 100 : 0;
            return {
                ...bucket,
                label: formatWeekRangeLabel(bucket.week_start),
                on_duty_hours: Number(onDutyHours.toFixed(2)),
                active_hours: Number(activeHours.toFixed(2)),
                effective_hours: Number(effectiveHours.toFixed(2)),
                on_duty_idle_hours: Number(onDutyIdleHours.toFixed(2)),
                active_off_duty_hours: Number(activeOffDutyHours.toFixed(2)),
                focus_ratio: Number(focusRatio.toFixed(1)),
                rating_avg: bucket.rating_count > 0 ? Number((bucket.rating_sum / bucket.rating_count).toFixed(1)) : null,
                is_partial: bucket.week_start === currentWeekStartKey,
            };
        });
}

function renderTable(data) {
    const body = document.getElementById("all-data-body");
    const rows = data.rows || [];
    body.innerHTML = rows.map((r) => {
        const rating = r.rating_avg == null ? "-" : `${r.rating_avg} (${r.rating_count})`;
        const onDutyH = Number(r.on_duty_hours || 0);
        const effectiveCell = onDutyH <= 0
            ? '<span style="font-size:1em">未出勤</span>'
            : `${escapeHtml(String(r.effective_hours || 0))}h`;
        return `<tr><td>${escapeHtml(r.date)}</td><td>${escapeHtml(String(r.total_work_time || "-"))}</td><td>${effectiveCell}</td><td>${escapeHtml(rating)}</td><td>${escapeHtml(String(r.segments || 0))}</td></tr>`;
    }).join("");
}

// ── 分享图方案注册表 ──────────────────────────────────
// 每套风格注册为 { weekly: (data)=>Promise<Blob>, daily: (data)=>Promise<Blob> }
// 导出时不提供选择器，从对应池中随机取一套风格。
const EXPORT_SCHEMES = {};

function registerExportScheme(id, impl) {
    EXPORT_SCHEMES[id] = impl;
}

function getExportComposer(view, schemeId) {
    const impl = EXPORT_SCHEMES[schemeId];
    const fn = impl && impl[view];
    return fn || null;
}

// 随机池：周图 = 用户选定的 3 款；日图 = 已确定 3 款（F-3 杂志暖色 / J-1 复古票据 / V-1 磁带随身听）
const WEEKLY_EXPORT_POOL = ["a2", "b1", "f1"];
const DAILY_EXPORT_POOL = ["f3", "j1", "v1"];

function pickRandomScheme(pool, view) {
    const available = pool.filter((id) => getExportComposer(view, id));
    if (available.length === 0) return null;
    return available[Math.floor(Math.random() * available.length)];
}

registerExportScheme("default", {
    weekly: (data) => composeWeeklySummaryBlob(data),
    daily: null,
});
// 周图：A-2 GitHub 热力横版 / B-1 Wrapped 竖版 / F-1 杂志暖色
registerExportScheme("a2", { weekly: composeWeeklyGithubBlob, daily: null });
registerExportScheme("b1", { weekly: composeWeeklyWrappedBlob, daily: null });
registerExportScheme("f1", { weekly: composeWeeklyMagazineBlob, daily: null });
// 日图（已确定 3 款）：F-3 杂志暖色 / J-1 复古票据 / V-1 磁带随身听
registerExportScheme("f3", { weekly: null, daily: composeDailyMagazineBlob });
registerExportScheme("j1", { weekly: null, daily: composeDailyReceiptBlob });
registerExportScheme("v1", { weekly: null, daily: composeDailyWalkmanBlob });

async function exportWeeklySummaryImage() {
    const btn = document.getElementById("btn-weekly-export");
    const weeklyData = window.__lastWeeklyData;
    if (!weeklyData) {
        alert("请先切换到周视图并等待数据加载完成");
        return;
    }
    if (btn) { btn.disabled = true; btn.querySelector("span").textContent = "生成中..."; }
    try {
        const schemeId = pickRandomScheme(WEEKLY_EXPORT_POOL, "weekly");
        if (!schemeId) throw new Error("未注册周分享图方案");
        const composer = getExportComposer("weekly", schemeId);
        const blob = await composer(weeklyData);
        triggerDownload(blob, `flowtrace-week-${weeklyData.week_start || fmtDate(new Date())}.png`);
    } catch (err) {
        console.error("生成周总结图片失败", err);
        alert("生成失败：" + (err?.message || err));
    } finally {
        if (btn) { btn.disabled = false; btn.querySelector("span").textContent = "保存本周总结为图片"; }
    }
}

async function exportDailySummaryImage() {
    const btn = document.getElementById("btn-daily-export");
    const dailyData = window.__lastDailyData;
    if (!dailyData) {
        alert("请先切换到日视图并等待数据加载完成");
        return;
    }
    if (btn) { btn.disabled = true; btn.querySelector("span").textContent = "生成中..."; }
    try {
        const schemeId = pickRandomScheme(DAILY_EXPORT_POOL, "daily");
        if (!schemeId) throw new Error("未注册日分享图方案");
        const composer = getExportComposer("daily", schemeId);
        const blob = await composer(dailyData);
        triggerDownload(blob, `flowtrace-day-${dailyData.date || fmtDate(new Date())}.png`);
    } catch (err) {
        console.error("生成今日分享图失败", err);
        alert("生成失败：" + (err?.message || err));
    } finally {
        if (btn) { btn.disabled = false; btn.querySelector("span").textContent = "保存今日分享图"; }
    }
}

async function composeWeeklySummaryBlob(weeklyData) {
    const W = 1200;
    const H = 1400;
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");

    // 背景渐变
    const bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#edf4ff");
    bg.addColorStop(0.5, "#f5f9ff");
    bg.addColorStop(1, "#fffdf4");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);

    // 顶部装饰光晕
    const halo = ctx.createRadialGradient(180, 120, 20, 180, 120, 400);
    halo.addColorStop(0, "rgba(83,134,228,0.24)");
    halo.addColorStop(1, "rgba(83,134,228,0)");
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, W, 400);

    const MARGIN = 56;
    let y = 60;

    // 标题
    ctx.fillStyle = "#233451";
    ctx.font = "bold 40px 'PingFang SC','Microsoft YaHei',sans-serif";
    ctx.textBaseline = "top";
    ctx.fillText("Flowtrace · 本周总结", MARGIN, y);
    y += 52;

    ctx.fillStyle = "#637493";
    ctx.font = "500 22px 'PingFang SC','Microsoft YaHei',sans-serif";
    const weekRange = `${weeklyData.week_start || ""} ~ ${weeklyData.week_end || ""}`;
    ctx.fillText(weekRange, MARGIN, y);
    ctx.textAlign = "right";
    const now = new Date();
    const stamp = `生成于 ${fmtDate(now)} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    ctx.fillText(stamp, W - MARGIN, y);
    ctx.textAlign = "left";
    y += 50;

    // 统计卡片
    y = drawStatsGrid(ctx, weeklyData, MARGIN, y, W - MARGIN * 2);

    y += 16;
    // 周时间轴
    y = drawWeekTimeline(ctx, weeklyData, MARGIN, y, W - MARGIN * 2);

    y += 18;
    // 把两张 ECharts 图并排画进去
    const chartY = y;
    const chartW = (W - MARGIN * 2 - 20) / 2;
    const chartH = 340;
    await drawChartSnapshot(ctx, weeklyProgressChart, MARGIN, chartY, chartW, chartH, "累计进度 vs 历史基线");
    await drawChartSnapshot(ctx, weeklyWowChart, MARGIN + chartW + 20, chartY, chartW, chartH, "每日环比 · 哑铃图");
    y = chartY + chartH + 40;

    // 底部 footer
    ctx.fillStyle = "#8a9ab5";
    ctx.font = "400 16px 'PingFang SC','Microsoft YaHei',sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Flowtrace — 时间有迹可循", W / 2, H - 44);
    ctx.textAlign = "left";

    return await new Promise((resolve, reject) => {
        canvas.toBlob((blob) => {
            if (blob) resolve(blob);
            else reject(new Error("canvas.toBlob 返回空"));
        }, "image/png");
    });
}

function drawRoundedRect(ctx, x, y, w, h, r) {
    const rad = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + rad, y);
    ctx.lineTo(x + w - rad, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + rad);
    ctx.lineTo(x + w, y + h - rad);
    ctx.quadraticCurveTo(x + w, y + h, x + w - rad, y + h);
    ctx.lineTo(x + rad, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - rad);
    ctx.lineTo(x, y + rad);
    ctx.quadraticCurveTo(x, y, x + rad, y);
    ctx.closePath();
}

function drawStatsGrid(ctx, weeklyData, x, y, width) {
    const days = weeklyData.days || [];
    const ratingValues = [];
    for (const d of days) {
        for (const r of d.ratings || []) ratingValues.push(r.value);
    }
    const avgRating = ratingValues.length
        ? (ratingValues.reduce((a, b) => a + b, 0) / ratingValues.length).toFixed(1)
        : "-";
    const daysWithData = weeklyData.days_with_data || 0;
    const totalHours = Number(weeklyData.total_hours || 0);
    const activeHours = Number(weeklyData.active_hours || 0);
    const effectiveHours = Number(weeklyData.effective_hours || 0);
    const focusRatio = Number(weeklyData.focus_ratio || 0);
    const avgOnDuty = daysWithData > 0 ? (totalHours / daysWithData).toFixed(1) : "-";
    const avgEffective = daysWithData > 0 ? (effectiveHours / daysWithData).toFixed(1) : "-";

    const cards = [
        { value: `${totalHours}h`, label: "总在岗", sub: `日均 ${avgOnDuty}h · 出勤 ${daysWithData}天` },
        { value: `${activeHours}h`, label: "总活跃", sub: `离岗活跃 ${Number(weeklyData.active_off_duty_hours || 0)}h` },
        { value: `${effectiveHours}h`, label: `在岗专注 · ${focusRatio}%`, sub: `日均 ${avgEffective}h` },
        { value: avgRating, label: "周均状态自评", sub: `${ratingValues.length} 次评价` },
    ];
    const gap = 14;
    const cardW = (width - gap * (cards.length - 1)) / cards.length;
    const cardH = 120;

    cards.forEach((c, i) => {
        const cx = x + (cardW + gap) * i;
        ctx.fillStyle = "rgba(255,255,255,0.92)";
        drawRoundedRect(ctx, cx, y, cardW, cardH, 14);
        ctx.fill();
        ctx.strokeStyle = "rgba(117,145,198,0.3)";
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.fillStyle = "#2c3f63";
        ctx.font = "bold 30px 'PingFang SC','Microsoft YaHei',sans-serif";
        ctx.fillText(c.value, cx + 18, y + 20);
        ctx.fillStyle = "#637493";
        ctx.font = "500 16px 'PingFang SC','Microsoft YaHei',sans-serif";
        ctx.fillText(c.label, cx + 18, y + 62);
        ctx.fillStyle = "#8a9ab5";
        ctx.font = "400 13px 'PingFang SC','Microsoft YaHei',sans-serif";
        ctx.fillText(c.sub, cx + 18, y + 88);
    });
    return y + cardH;
}

function drawWeekTimeline(ctx, weeklyData, x, y, width) {
    const labelColWidth = 120;
    const trackX = x + labelColWidth;
    const trackWidth = width - labelColWidth;
    const rowH = 30;
    const days = weeklyData.days || [];

    // 卡片背景
    const blockH = 30 + rowH * days.length + 40;
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    drawRoundedRect(ctx, x, y, width, blockH, 14);
    ctx.fill();
    ctx.strokeStyle = "rgba(117,145,198,0.3)";
    ctx.stroke();

    // 标题
    ctx.fillStyle = "#334867";
    ctx.font = "600 18px 'PingFang SC','Microsoft YaHei',sans-serif";
    ctx.fillText("每日时间轴", x + 18, y + 14);

    // 时间坐标
    ctx.fillStyle = "#637493";
    ctx.font = "400 12px 'PingFang SC','Microsoft YaHei',sans-serif";
    const ticks = ["00", "06", "12", "18", "24"];
    ticks.forEach((t, i) => {
        const tx = trackX + (trackWidth / (ticks.length - 1)) * i;
        ctx.textAlign = i === 0 ? "left" : i === ticks.length - 1 ? "right" : "center";
        ctx.fillText(t, tx, y + 18);
    });
    ctx.textAlign = "left";

    days.forEach((day, idx) => {
        const ry = y + 40 + idx * rowH;
        const dateObj = new Date(`${day.date}T00:00:00`);
        const weekday = WEEKDAY_CN[dateObj.getDay()];
        const labelColor = heatColor(Number(day.active_minutes || 0), day.date);
        ctx.fillStyle = labelColor;
        ctx.font = "600 15px 'PingFang SC','Microsoft YaHei',sans-serif";
        ctx.textAlign = "right";
        ctx.fillText(weekday, x + 70, ry + 5);
        ctx.fillStyle = "#637493";
        ctx.font = "400 12px 'PingFang SC','Microsoft YaHei',sans-serif";
        ctx.fillText(day.date.slice(5), x + labelColWidth - 12, ry + 8);
        ctx.textAlign = "left";

        // 背景
        ctx.fillStyle = "rgba(238,243,251,0.92)";
        drawRoundedRect(ctx, trackX, ry, trackWidth, 20, 5);
        ctx.fill();

        for (const seg of day.timeline_segments || []) {
            const start = Number(seg.start_min || 0);
            const end = Number(seg.end_min || 0);
            if (end <= start) continue;
            const segColor = weekSegmentColor(seg);
            if (!segColor) continue;
            const sx = trackX + (start / 1440) * trackWidth;
            const sw = ((end - start) / 1440) * trackWidth;
            ctx.fillStyle = segColor;
            ctx.fillRect(sx, ry, Math.max(sw, 1), 20);
        }

        for (const mark of day.checkin_marks || []) {
            const mins = toMinutes(mark.time);
            if (mins == null) continue;
            const mx = trackX + (mins / 1440) * trackWidth;
            ctx.fillStyle = mark.inferred ? "rgba(124,143,174,0.7)" : "#4f82de";
            ctx.fillRect(mx - 0.5, ry - 2, 1.5, 24);
        }
    });
    return y + blockH;
}

async function drawChartSnapshot(ctx, chart, x, y, w, h, title) {
    // 卡片背景 + 标题
    ctx.fillStyle = "rgba(255,255,255,0.92)";
    drawRoundedRect(ctx, x, y, w, h, 14);
    ctx.fill();
    ctx.strokeStyle = "rgba(117,145,198,0.3)";
    ctx.stroke();
    ctx.fillStyle = "#334867";
    ctx.font = "600 16px 'PingFang SC','Microsoft YaHei',sans-serif";
    ctx.fillText(title, x + 16, y + 14);

    if (!chart) return;
    try {
        const dataUrl = chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "rgba(255,255,255,0)" });
        const img = await loadImage(dataUrl);
        const innerPad = 12;
        const drawY = y + 42;
        const drawH = h - 52;
        const drawX = x + innerPad;
        const drawW = w - innerPad * 2;
        // 保持宽高比，按高度缩放
        const ratio = img.width / img.height;
        let renderW = drawW;
        let renderH = drawW / ratio;
        if (renderH > drawH) {
            renderH = drawH;
            renderW = drawH * ratio;
        }
        const offX = drawX + (drawW - renderW) / 2;
        const offY = drawY + (drawH - renderH) / 2;
        ctx.drawImage(img, offX, offY, renderW, renderH);
    } catch (err) {
        console.warn("drawChartSnapshot failed", err);
    }
}

function loadImage(src) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = src;
    });
}

function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ═══════════════ 风格化分享图方案（Canvas 实现） ═══════════════
// 尺寸为预览稿 CSS 像素 × 2，导出 2x 高清 PNG。

const EXPORT_FONT = "'PingFang SC','Microsoft YaHei',sans-serif";

function createExportCanvas(w, h) {
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.textBaseline = "top";
    return { canvas, ctx };
}

function canvasBlob(canvas) {
    return new Promise((resolve, reject) => {
        canvas.toBlob((blob) => {
            if (blob) resolve(blob);
            else reject(new Error("canvas.toBlob 返回空"));
        }, "image/png");
    });
}

function exportFont(weight, px) {
    return `${weight} ${px}px ${EXPORT_FONT}`;
}

// 从每天 timeline_segments 计算 8:00-20:00 每小时活跃密度（0-4 级）
function buildHourDensity(weeklyData) {
    const rows = [];
    (weeklyData.days || []).slice(0, 7).forEach((d) => {
        const activeMin = new Array(24).fill(0);
        for (const seg of d.timeline_segments || []) {
            const cls = classifyDailySegment(seg);
            if (cls !== "activeOnDuty" && cls !== "activeOffDuty") continue;
            const start = Number(seg.start_min || 0);
            const end = Number(seg.end_min || 0);
            for (let h = 0; h < 24; h++) {
                const hs = h * 60;
                const lo = Math.max(start, hs);
                const hi = Math.min(end, hs + 60);
                if (hi > lo) activeMin[h] += hi - lo;
            }
        }
        const row = [];
        for (let h = 8; h < 20; h++) {
            const m = activeMin[h];
            row.push(m >= 55 ? 4 : m >= 40 ? 3 : m >= 20 ? 2 : m >= 5 ? 1 : 0);
        }
        rows.push(row);
    });
    return rows;
}

function exportSegmentClass(seg) {
    return classifyDailySegment(seg);
}

// ── A-2 · GitHub 热力横版（周图）────────────────────────
async function composeWeeklyGithubBlob(weeklyData) {
    const S = 2, W = 580 * S, H = 420 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    ctx.fillStyle = "#0d1117";
    ctx.fillRect(0, 0, W, H);
    const halo = ctx.createRadialGradient(W - 260, -120, 20, W - 260, -120, 420);
    halo.addColorStop(0, "rgba(63,185,80,0.10)");
    halo.addColorStop(1, "rgba(63,185,80,0)");
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, W, H);

    const rangeText = `${(weeklyData.week_start || "").slice(5)} — ${(weeklyData.week_end || "").slice(5)}`;
    // 顶部标题 + badge
    ctx.font = exportFont(700, 15 * S);
    ctx.fillStyle = "#e6edf3";
    ctx.fillText("WEEKLY CONTRIBUTION", 60, 63);
    ctx.font = exportFont(500, 12 * S);
    const badgeTw = ctx.measureText(rangeText).width;
    const badgeW = badgeTw + 32 * S;
    const badgeX = W - 60 - badgeW;
    drawRoundedRect(ctx, badgeX, 44, badgeW, 34 * S, 17 * S);
    ctx.fillStyle = "#161b22";
    ctx.fill();
    ctx.strokeStyle = "#21262d";
    ctx.lineWidth = 1 * S;
    ctx.stroke();
    ctx.fillStyle = "#8b949e";
    ctx.textAlign = "center";
    ctx.fillText(rangeText, badgeX + badgeW / 2, 44 + (34 * S - 12 * S) / 2);
    ctx.textAlign = "left";

    // 小时刻度 + 网格
    const gridX = 76 * S, gridY = 138 * S;
    const sideW = 170 * S;
    const areaW = W - gridX - sideW;
    const gap = 6 * S, n = 12;
    const cellW = (areaW - gap * (n - 1) - 12 * S) / n;
    const cellH = cellW;
    const rowGap = 8 * S;
    const dayKeys = ["一", "二", "三", "四", "五", "六", "日"];
    const gColors = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"];
    const density = buildHourDensity(weeklyData);

    ctx.font = exportFont(500, 9 * S);
    ctx.fillStyle = "#30363d";
    ctx.textAlign = "center";
    for (let i = 0; i < n; i++) {
        ctx.fillText(String(8 + i), gridX + i * (cellW + gap) + cellW / 2, gridY - 26 * S);
    }
    ctx.textAlign = "left";

    density.forEach((row, ri) => {
        ctx.font = exportFont(600, 11 * S);
        ctx.fillStyle = "#484f58";
        ctx.textAlign = "right";
        ctx.fillText(dayKeys[ri] || "", gridX - 16 * S, gridY + ri * (cellH + rowGap) + (cellH - 11 * S) / 2);
        ctx.textAlign = "left";
        row.forEach((lv, ci) => {
            const x = gridX + ci * (cellW + gap);
            const y = gridY + ri * (cellH + rowGap);
            drawRoundedRect(ctx, x, y, cellW, cellH, 2.5 * S);
            ctx.fillStyle = gColors[lv] || gColors[0];
            ctx.fill();
        });
    });

    // 右侧指标栏
    const barX = gridX + areaW + 12 * S;
    ctx.strokeStyle = "#21262d";
    ctx.lineWidth = 1.5 * S;
    ctx.beginPath();
    ctx.moveTo(barX, 120 * S);
    ctx.lineTo(barX, H - 60 * S);
    ctx.stroke();
    const metrics = [
        { v: `${Number(weeklyData.effective_hours || 0).toFixed(1)}`, u: "h", l: "有效专注" },
        { v: `${Number(weeklyData.total_hours || 0).toFixed(1)}`, u: "h", l: "总在岗" },
        { v: `${Number(weeklyData.focus_ratio || 0).toFixed(0)}`, u: "%", l: "专注率" },
        { v: `${weeklyData.rating_avg != null ? Number(weeklyData.rating_avg).toFixed(1) : "-"}`, u: "/5", l: "周均状态自评" },
    ];
    metrics.forEach((m, i) => {
        const y = 230 + i * 140;
        ctx.font = exportFont(800, 26 * S);
        const vw = ctx.measureText(m.v).width; // 与数值同字号测量，避免单位文本偏移不足
        ctx.fillStyle = "#e6edf3";
        ctx.fillText(m.v, barX + 24 * S, y);
        ctx.fillStyle = "#3fb950";
        ctx.font = exportFont(600, 13 * S);
        ctx.fillText(m.u, barX + 24 * S + vw + 8 * S, y + 8 * S);
        ctx.fillStyle = "#484f58";
        ctx.font = exportFont(500, 11 * S);
        ctx.fillText(m.l, barX + 24 * S, y + 34 * S);
    });

    // footer
    ctx.fillStyle = "#30363d";
    ctx.font = exportFont(400, 11 * S);
    ctx.fillText("FLOWTRACE · 时间有迹可循", 60, H - 40 * S);
    ctx.textAlign = "right";
    ctx.fillText("深色时段 = 高专注", W - 60, H - 40 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── B-1 · Wrapped 竖版（周图）───────────────────────────
async function composeWeeklyWrappedBlob(weeklyData) {
    const S = 2, W = 480 * S, H = 820 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    const bg = ctx.createLinearGradient(0, 0, W * 0.35, H);
    bg.addColorStop(0, "#1a0a2e");
    bg.addColorStop(0.4, "#16213e");
    bg.addColorStop(1, "#0a1628");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    let g = ctx.createRadialGradient(0, 0, 20, 0, 0, 320 * S);
    g.addColorStop(0, "rgba(29,185,84,0.20)");
    g.addColorStop(1, "rgba(29,185,84,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
    g = ctx.createRadialGradient(W, H, 20, W, H, 300 * S);
    g.addColorStop(0, "rgba(139,92,246,0.15)");
    g.addColorStop(1, "rgba(139,92,246,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    const rangeText = `${(weeklyData.week_start || "").slice(5).replace("-", ".")} — ${(weeklyData.week_end || "").slice(5).replace("-", ".")}`;
    ctx.font = exportFont(700, 12 * S);
    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.fillText("FLOWTRACE", 64, 80);
    ctx.font = exportFont(500, 12 * S);
    const badgeTw = ctx.measureText(rangeText).width;
    const bw = badgeTw + 32 * S;
    const badgeX = W - 64 - bw;
    drawRoundedRect(ctx, badgeX, 74, bw, 34 * S, 17 * S);
    ctx.fillStyle = "rgba(255,255,255,0.06)";
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    ctx.textAlign = "center";
    ctx.fillText(rangeText, badgeX + bw / 2, 74 + (34 * S - 12 * S) / 2);
    ctx.textAlign = "left";

    // hero
    const eff = Number(weeklyData.effective_hours || 0).toFixed(1);
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    ctx.font = exportFont(500, 13 * S);
    ctx.fillText("本周有效工时", W / 2, 210);
    const numY = 250;
    const grad = ctx.createLinearGradient(0, numY, 0, numY + 180 * S);
    grad.addColorStop(0, "#ffffff");
    grad.addColorStop(1, "rgba(255,255,255,0.5)");
    ctx.fillStyle = grad;
    ctx.font = exportFont(900, 96 * S);
    ctx.fillText(eff, W / 2, numY);
    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.font = exportFont(500, 18 * S);
    ctx.fillText("hours", W / 2, numY + 100 * S);
    const ratioTxt = `专注率 ${Number(weeklyData.focus_ratio || 0).toFixed(0)}% · 上周 +${Number(weeklyData.delta_hours || 0).toFixed(1)}h`;
    const rw = 14 * S * String(ratioTxt).length * 0.95 + 56 * S;
    drawRoundedRect(ctx, W / 2 - rw / 2, numY + 125 * S, rw, 40 * S, 20 * S);
    ctx.fillStyle = "rgba(29,185,84,0.18)";
    ctx.fill();
    ctx.strokeStyle = "rgba(29,185,84,0.35)";
    ctx.lineWidth = 1 * S;
    ctx.stroke();
    ctx.fillStyle = "#1db954";
    ctx.font = exportFont(600, 14 * S);
    ctx.fillText(ratioTxt, W / 2, numY + 138 * S);
    ctx.textAlign = "left";

    // 每日条形
    const days = weeklyData.days || [];
    const maxMin = 540;
    const barX = 96 * S, barW = W - 192 * S, barY = numY + 170 * S, rowH = 44 * S, rowGap = 8 * S;
    const dayKeys = ["一", "二", "三", "四", "五", "六", "日"];
    days.slice(0, 7).forEach((d, i) => {
        const effMin = Number(d.effective_minutes || 0);
        const y = barY + i * (rowH + rowGap);
        ctx.fillStyle = "rgba(255,255,255,0.3)";
        ctx.font = exportFont(600, 12 * S);
        ctx.textAlign = "right";
        ctx.fillText(dayKeys[i] || "", barX - 20 * S, y + 14 * S);
        ctx.textAlign = "left";
        drawRoundedRect(ctx, barX, y, barW, rowH, 6 * S);
        ctx.fillStyle = "rgba(255,255,255,0.04)";
        ctx.fill();
        if (effMin > 0) {
            const pct = Math.min(1, effMin / maxMin);
            let fill = null;
            if (effMin >= 420) fill = ctx.createLinearGradient(barX, 0, barX + barW * pct, 0), fill.addColorStop(0, "#1db954"), fill.addColorStop(1, "#1ed760");
            else if (effMin >= 240) fill = ctx.createLinearGradient(barX, 0, barX + barW * pct, 0), fill.addColorStop(0, "#6366f1"), fill.addColorStop(1, "#818cf8");
            else fill = ctx.createLinearGradient(barX, 0, barX + barW * pct, 0), fill.addColorStop(0, "#f59e0b"), fill.addColorStop(1, "#fbbf24");
            const fillW = Math.max(barW * pct, 8 * S);
            drawRoundedRect(ctx, barX, y, fillW, rowH, 6 * S);
            ctx.fillStyle = fill;
            ctx.fill();
            const label = `${(effMin / 60).toFixed(1)}h`;
            ctx.font = exportFont(700, 11 * S);
            const lw = ctx.measureText(label).width;
            if (fillW >= lw + 40 * S) {
                // 柱内右对齐
                ctx.fillStyle = "rgba(255,255,255,0.9)";
                ctx.fillText(label, barX + fillW - lw - 10 * S, y + 17 * S);
            } else {
                // 短柱：数值放柱外右侧
                ctx.fillStyle = "rgba(255,255,255,0.75)";
                ctx.fillText(label, barX + fillW + 10 * S, y + 17 * S);
            }
        } else {
            drawRoundedRect(ctx, barX, y, barW, rowH, 6 * S);
            ctx.fillStyle = "rgba(255,255,255,0.08)";
            ctx.fill();
        }
    });

    // 底部统计
    const bottomY = barY + 7 * (rowH + rowGap) + 40 * S;
    const stats = [
        { v: `${Number(weeklyData.total_hours || 0).toFixed(1)}h`, l: "总在岗" },
        { v: `${weeklyData.days_with_data || 0}/7`, l: "出勤" },
        { v: `${weeklyData.rating_avg != null ? Number(weeklyData.rating_avg).toFixed(1) : "-"}`, l: "均分" },
    ];
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.beginPath();
    ctx.moveTo(64, bottomY);
    ctx.lineTo(W - 64, bottomY);
    ctx.stroke();
    stats.forEach((s, i) => {
        const x = W / 2 + (i - 1) * 160 * S;
        ctx.textAlign = "center";
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(800, 22 * S);
        ctx.fillText(s.v, x, bottomY + 22 * S);
        ctx.fillStyle = "rgba(255,255,255,0.3)";
        ctx.font = exportFont(500, 11 * S);
        ctx.fillText(s.l, x, bottomY + 58 * S);
    });
    ctx.textAlign = "left";
    ctx.fillStyle = "rgba(255,255,255,0.15)";
    ctx.font = exportFont(400, 11 * S);
    ctx.textAlign = "center";
    ctx.fillText("时间有迹可循", W / 2, H - 36 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── F-1 · 杂志暖色封面（周图）───────────────────────────
async function composeWeeklyMagazineBlob(weeklyData) {
    const S = 2, W = 480 * S, H = 760 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    const SERIF = "'Times New Roman',Georgia,'SimSun','FangSong',serif";
    const serifFont = (weight, px) => `${weight} ${px}px ${SERIF}`;
    const bg = ctx.createLinearGradient(0, 0, W * 0.4, H);
    bg.addColorStop(0, "#ff4d2e");
    bg.addColorStop(0.48, "#ff2e63");
    bg.addColorStop(1, "#5b2a86");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    let g = ctx.createRadialGradient(W, 0, 20, W, 0, 340 * S);
    g.addColorStop(0, "rgba(255,255,255,0.18)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
    g = ctx.createRadialGradient(0, H, 20, 0, H, 300 * S);
    g.addColorStop(0, "rgba(255,214,64,0.16)");
    g.addColorStop(1, "rgba(255,214,64,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    // 顶部
    ctx.fillStyle = "#ffffff";
    ctx.font = exportFont(700, 16 * S);
    ctx.fillText("FLOWTRACE", 52 * S, 36 * S);
    const rangeText = `${(weeklyData.week_start || "").slice(5).replace("-", ".")} — ${(weeklyData.week_end || "").slice(5).replace("-", ".")}`;
    const pillFont = 13 * S;
    ctx.font = exportFont(700, pillFont);
    const pillTw = ctx.measureText(rangeText).width;
    const bw = pillTw + 48 * S;
    const pillX = W - 52 * S - bw, pillY = 30 * S, pillH = 44 * S;
    drawRoundedRect(ctx, pillX, pillY, bw, pillH, pillH / 2);
    ctx.strokeStyle = "rgba(255,255,255,0.55)";
    ctx.lineWidth = 1.5 * S;
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.fillText(rangeText, pillX + bw / 2, pillY + (pillH - pillFont) / 2);
    ctx.textAlign = "left";

    // hero
    const eff = Number(weeklyData.effective_hours || 0).toFixed(1);
    const focusVal = Number(weeklyData.focus_ratio || 0);
    ctx.fillStyle = "rgba(255,255,255,0.75)";
    ctx.font = serifFont(700, 13 * S);
    ctx.fillText("MY WEEK · IN FOCUS", 52 * S, 93 * S);
    ctx.fillStyle = "#ffffff";
    ctx.shadowColor = "rgba(0,0,0,0.28)";
    ctx.shadowBlur = 28 * S;
    ctx.font = exportFont(900, 118 * S);
    ctx.fillText(eff, 52 * S, 118 * S);
    ctx.shadowBlur = 0;
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.font = serifFont(700, 26 * S);
    ctx.fillText("HOURS OF DEEP FOCUS", 52 * S, 248 * S);
    const focusW = ctx.measureText("HOURS OF DEEP FOCUS").width;
    const subTxt = `专注率 ${focusVal.toFixed(0)}% ↑ 上周 +${Number(weeklyData.delta_hours || 0).toFixed(1)}h`;
    const chipFont = 14.5 * S;
    ctx.font = exportFont(700, chipFont);
    const chipTw = ctx.measureText(subTxt).width;
    const chipW2 = chipTw + 64 * S, chipH2 = chipFont + 30 * S;
    ctx.save();
    ctx.translate(52 * S, 286 * S);
    ctx.rotate(-1.5 * Math.PI / 180);
    drawRoundedRect(ctx, 0, 0, chipW2, chipH2, chipH2 / 2);
    ctx.fillStyle = "#ffd640";
    ctx.fill();
    ctx.fillStyle = "#3a1f00";
    ctx.textAlign = "center";
    ctx.fillText(subTxt, chipW2 / 2, 15 * S);
    ctx.textAlign = "left";
    ctx.restore();
    // 评分/标题两行右侧大 emoji：按周专注率（🔥≥90 ⚡≥70 ☕≥50 😴<50）
    const heroEmoji = focusVal >= 90 ? "🔥" : focusVal >= 70 ? "⚡" : focusVal >= 50 ? "☕" : "😴";
    ctx.font = `${72 * S}px ${EXPORT_FONT}`;
    ctx.fillText(heroEmoji, 52 * S + Math.max(focusW, chipW2) + 12 * S, 253 * S);

    // 每日条形
    const days = weeklyData.days || [];
    const maxMin = 480;
    const areaX = 52 * S, areaW = W - 104 * S;
    const colGap = 16 * S, n = 7;
    const colW = (areaW - colGap * (n - 1)) / n;
    const trackY = 385 * S, trackH = 150 * S;
    const todayStr = fmtDate(new Date());
    ctx.fillStyle = "rgba(255,255,255,0.65)";
    ctx.font = serifFont(700, 11 * S);
    ctx.fillText("DAILY FOCUS HOURS", areaX, 345 * S);
    days.slice(0, 7).forEach((d, i) => {
        const effMin = Number(d.effective_minutes || 0);
        const cx = areaX + i * (colW + colGap);
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(800, 11 * S);
        ctx.textAlign = "center";
        ctx.fillText(effMin > 0 ? `${(effMin / 60).toFixed(1)}` : "—", cx + colW / 2, trackY - 17 * S);
        ctx.textAlign = "left";
        drawRoundedRect(ctx, cx, trackY, colW, trackH, 8 * S);
        ctx.fillStyle = "rgba(255,255,255,0.14)";
        ctx.fill();
        if (effMin > 0) {
            const fh = Math.max(8 * S, Math.min(trackH - 6 * S, trackH * (effMin / maxMin)));
            drawRoundedRect(ctx, cx + 6 * S, trackY + trackH - fh, colW - 12 * S, fh - 6 * S, 5 * S);
            ctx.fillStyle = d.date === todayStr ? "#ffd640" : "#ffffff";
            ctx.fill();
        }
        const dayKeys = ["一", "二", "三", "四", "五", "六", "日"];
        ctx.fillStyle = "rgba(255,255,255,0.6)";
        ctx.font = serifFont(500, 9 * S);
        ctx.textAlign = "center";
        ctx.fillText(dayKeys[i] || "", cx + colW / 2, trackY + trackH + 14 * S);
        ctx.textAlign = "left";
    });

    // 底部统计
    const statsY = trackY + trackH + 60 * S;
    const stats = [
        { v: `${Number(weeklyData.total_hours || 0).toFixed(1)}h`, l: "总在岗" },
        { v: `${weeklyData.days_with_data || 0} 天`, l: "出勤" },
        { v: `${weeklyData.rating_avg != null ? Number(weeklyData.rating_avg).toFixed(1) : "-"}`, l: "周均状态自评" },
    ];
    const cardW = (areaW - 2 * 28 * S) / 3;
    stats.forEach((s, i) => {
        const cx = areaX + i * (cardW + 28 * S);
        drawRoundedRect(ctx, cx, statsY, cardW, 88 * S, 14 * S);
        ctx.fillStyle = "rgba(255,255,255,0.14)";
        ctx.fill();
        ctx.strokeStyle = "rgba(255,255,255,0.22)";
        ctx.lineWidth = 1 * S;
        ctx.stroke();
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(900, 24 * S);
        ctx.textAlign = "center";
        ctx.fillText(s.v, cx + cardW / 2, statsY + 23 * S);
        ctx.fillStyle = "rgba(255,255,255,0.75)";
        ctx.font = serifFont(600, 10 * S);
        ctx.fillText(s.l, cx + cardW / 2, statsY + 55 * S);
        ctx.textAlign = "left";
    });
    ctx.fillStyle = "rgba(255,255,255,0.6)";
    ctx.font = serifFont(600, 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("TIME LEAVES ITS TRACE · 时间有迹可循", W / 2, H - 48 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── F-3 · 杂志暖色日卡（日图）───────────────────────────
async function composeDailyMagazineBlob(dailyData) {
    const S = 2, W = 480 * S, H = 780 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    const SERIF = "'Times New Roman',Georgia,'SimSun','FangSong',serif";
    const serifFont = (weight, px) => `${weight} ${px}px ${SERIF}`;
    const bg = ctx.createLinearGradient(0, 0, W * 0.4, H);
    bg.addColorStop(0, "#ffb800");
    bg.addColorStop(0.5, "#ff6a00");
    bg.addColorStop(1, "#d92e2e");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    let g = ctx.createRadialGradient(W, 0, 20, W, 0, 340 * S);
    g.addColorStop(0, "rgba(255,255,255,0.18)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
    g = ctx.createRadialGradient(0, H, 20, 0, H, 300 * S);
    g.addColorStop(0, "rgba(255,214,64,0.16)");
    g.addColorStop(1, "rgba(255,214,64,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    const dateStr = dailyData.date || fmtDate(new Date());
    const weekday = WEEKDAY_CN[new Date(`${dateStr}T00:00:00`).getDay()] || "";
    const dayNum = String(dateStr).slice(8);
    const rangeText = `${weekday} · ${String(dateStr).slice(5).replace("-", ".")}`;
    ctx.fillStyle = "#ffffff";
    ctx.font = exportFont(700, 16 * S);
    ctx.fillText("FLOWTRACE", 52 * S, 36 * S);
    const pillFont = 13 * S;
    ctx.font = exportFont(700, pillFont);
    const pillTw = ctx.measureText(rangeText).width;
    const bw = pillTw + 48 * S;
    const pillX = W - 52 * S - bw, pillY = 30 * S, pillH = 44 * S;
    drawRoundedRect(ctx, pillX, pillY, bw, pillH, pillH / 2);
    ctx.strokeStyle = "rgba(255,255,255,0.55)";
    ctx.lineWidth = 1.5 * S;
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.fillText(rangeText, pillX + bw / 2, pillY + (pillH - pillFont) / 2);
    ctx.textAlign = "left";

    // hero
    const ratings = dailyData.ratings || [];
    const avg = ratings.length ? (ratings.reduce((s, r) => s + Number(r.value || 0), 0) / ratings.length).toFixed(1) : "-";
    const eff = Number(dailyData.effective_minutes || 0) / 60;
    const focusVal = Number(dailyData.focus_ratio || 0);
    const focus = focusVal.toFixed(0);
    ctx.fillStyle = "rgba(255,255,255,0.75)";
    ctx.font = serifFont(700, 13 * S);
    ctx.fillText(`DAY ${dayNum} · IN FOCUS`, 52 * S, 93 * S);
    ctx.fillStyle = "#ffffff";
    ctx.shadowColor = "rgba(0,0,0,0.28)";
    ctx.shadowBlur = 28 * S;
    ctx.font = exportFont(900, 118 * S);
    ctx.fillText(eff.toFixed(1), 52 * S, 118 * S);
    ctx.shadowBlur = 0;
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.font = serifFont(700, 26 * S);
    ctx.fillText("FOCUS HOURS TODAY", 52 * S, 248 * S);
    const focusW = ctx.measureText("FOCUS HOURS TODAY").width;
    const subTxt = `状态自评 ${avg} ★ · 专注率 ${focus}%`;
    const chipFont = 14.5 * S;
    ctx.font = exportFont(700, chipFont);
    const chipTw = ctx.measureText(subTxt).width;
    const chipW2 = chipTw + 64 * S, chipH2 = chipFont + 30 * S;
    ctx.save();
    ctx.translate(52 * S, 286 * S);
    ctx.rotate(-1.5 * Math.PI / 180);
    drawRoundedRect(ctx, 0, 0, chipW2, chipH2, chipH2 / 2);
    ctx.fillStyle = "#ffd640";
    ctx.fill();
    ctx.fillStyle = "#3a1f00";
    ctx.textAlign = "center";
    ctx.fillText(subTxt, chipW2 / 2, 15 * S);
    ctx.textAlign = "left";
    ctx.restore();
    // 评分/标题两行的右侧大 emoji：按专注率选（🔥≥90 ⚡≥70 ☕≥50 😴<50）
    const heroEmoji = focusVal >= 90 ? "🔥" : focusVal >= 70 ? "⚡" : focusVal >= 50 ? "☕" : "😴";
    ctx.font = `${72 * S}px ${EXPORT_FONT}`;
    ctx.fillText(heroEmoji, 52 * S + Math.max(focusW, chipW2) + 12 * S, 253 * S);

    // 时间轴
    const segColors = {
        activeOnDuty: "#ffd640",
        onDutyIdle: "rgba(255,255,255,0.85)",
        activeOffDuty: "#ff9e8a",
        offDutyIdle: "rgba(255,255,255,0.30)",
    };
    const tlX = 52 * S, tlW = W - 104 * S, tlY = 348 * S, tlH = 104 * S;
    drawRoundedRect(ctx, tlX, tlY, tlW, tlH, 12 * S);
    ctx.fillStyle = "rgba(0,0,0,0.14)";
    ctx.fill();
    ctx.save();
    drawRoundedRect(ctx, tlX, tlY, tlW, tlH, 12 * S);
    ctx.clip();
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        if (end <= start) continue;
        const x = tlX + (start / 1440) * tlW;
        const w = ((end - start) / 1440) * tlW;
        ctx.fillStyle = segColors[cls] || "rgba(255,255,255,0.30)";
        ctx.fillRect(x, tlY + 4 * S, Math.max(w, 1 * S), tlH - 8 * S);
    }
    ctx.restore();
    ctx.fillStyle = "rgba(255,255,255,0.6)";
    ctx.font = serifFont(700, 10 * S);
    ctx.textAlign = "left";
    ctx.fillText("00", tlX, tlY + tlH + 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("06", tlX + tlW * 0.25, tlY + tlH + 10 * S);
    ctx.fillText("12", tlX + tlW * 0.5, tlY + tlH + 10 * S);
    ctx.fillText("18", tlX + tlW * 0.75, tlY + tlH + 10 * S);
    ctx.textAlign = "right";
    ctx.fillText("24", tlX + tlW, tlY + tlH + 10 * S);
    ctx.textAlign = "left";

    // 打卡芯片
    const marks = dailyData.checkin_marks || [];
    const chipY = tlY + tlH + 46 * S, chipH = 80 * S;
    const chipW = (tlW - 20 * S) / 2;
    const markLabels = { clock_in: "CLOCK IN", clock_out: "CLOCK OUT", in: "CLOCK IN", out: "CLOCK OUT" };
    marks.slice(0, 2).forEach((m, i) => {
        const cx = tlX + i * (chipW + 20 * S);
        drawRoundedRect(ctx, cx, chipY, chipW, chipH, 10 * S);
        ctx.fillStyle = "rgba(255,255,255,0.14)";
        ctx.fill();
        const isOut = String(m.action || "").includes("out");
        drawRoundedRect(ctx, cx + 12 * S, chipY + 16 * S, 4 * S, chipH - 32 * S, 2 * S);
        ctx.fillStyle = isOut ? "#ff9e8a" : "#ffd640";
        ctx.fill();
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(900, 20 * S);
        ctx.textAlign = "center";
        ctx.fillText(m.time || "--:--", cx + chipW / 2, chipY + 20 * S);
        ctx.fillStyle = "rgba(255,255,255,0.7)";
        ctx.font = serifFont(700, 11 * S);
        const markLabel = exportCheckinLabel(m, markLabels);
        ctx.fillText(markLabel, cx + chipW / 2, chipY + 52 * S);
        ctx.textAlign = "left";
    });

    // 底部统计
    const statsY = chipY + chipH + 44 * S;
    const onDutyH = (Number(dailyData.on_duty_minutes || 0) / 60).toFixed(1);
    const activeH = (Number(dailyData.active_minutes || 0) / 60).toFixed(1);
    const slackMin = Math.max(0, Number(dailyData.on_duty_minutes || 0) - Number(dailyData.effective_minutes || 0));
    const stats = [
        { v: `${onDutyH}h`, l: "在岗" },
        { v: `${activeH}h`, l: "活跃" },
        { v: `${slackMin}m`, l: "摸鱼" },
    ];
    const cardW = (tlW - 2 * 28 * S) / 3;
    stats.forEach((s, i) => {
        const cx = tlX + i * (cardW + 28 * S);
        drawRoundedRect(ctx, cx, statsY, cardW, 88 * S, 14 * S);
        ctx.fillStyle = "rgba(255,255,255,0.14)";
        ctx.fill();
        ctx.strokeStyle = "rgba(255,255,255,0.22)";
        ctx.lineWidth = 1 * S;
        ctx.stroke();
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(900, 24 * S);
        ctx.textAlign = "center";
        ctx.fillText(s.v, cx + cardW / 2, statsY + 23 * S);
        ctx.fillStyle = "rgba(255,255,255,0.75)";
        ctx.font = serifFont(600, 10 * S);
        ctx.fillText(s.l, cx + cardW / 2, statsY + 55 * S);
        ctx.textAlign = "left";
    });
    ctx.fillStyle = "rgba(255,255,255,0.6)";
    ctx.font = serifFont(600, 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("TIME LEAVES ITS TRACE · 时间有迹可循", W / 2, H - 48 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── H · 时钟表盘（日图）────────────────────────────────
// ── H · 时钟表盘（日图）────────────────────────────────
async function composeDailyClockBlob(dailyData) {
    const S = 2, W = 480 * S, H = 780 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    const bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#10162a");
    bg.addColorStop(0.55, "#16213e");
    bg.addColorStop(1, "#0d1220");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    const halo = ctx.createRadialGradient(W / 2, 400, 20, W / 2, 400, 500);
    halo.addColorStop(0, "rgba(96,165,250,0.10)");
    halo.addColorStop(1, "rgba(96,165,250,0)");
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, W, H);

    const dateStr = dailyData.date || fmtDate(new Date());
    const weekday = WEEKDAY_CN[new Date(`${dateStr}T00:00:00`).getDay()] || "";
    const rangeText = `${weekday} · ${dateStr}`;
    ctx.fillStyle = "rgba(255,255,255,0.45)";
    ctx.font = exportFont(700, 12 * S);
    ctx.fillText("FLOWTRACE", 64, 64);
    const bw = 11 * S * String(rangeText).length * 0.9 + 48 * S;
    drawRoundedRect(ctx, W - 64 - bw, 56, bw, 36 * S, 18 * S);
    ctx.fillStyle = "rgba(255,255,255,0.06)";
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.font = exportFont(500, 11 * S);
    ctx.fillText(rangeText, W - 64 - bw + 20 * S, 64);

    // 24h 表盘（canvas 绝对坐标）
    const cx = W / 2, cy = 540;
    const R = 340, Rinner = 196;
    const segColors = {
        activeOnDuty: "#34d399",
        onDutyIdle: "#fbbf24",
        activeOffDuty: "#60a5fa",
        offDutyIdle: "rgba(255,255,255,0.06)",
    };
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(255,255,255,0.07)";
    ctx.lineWidth = 52;
    ctx.stroke();
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        if (end <= start) continue;
        const a0 = -Math.PI / 2 + (start / 1440) * Math.PI * 2;
        const a1 = -Math.PI / 2 + (end / 1440) * Math.PI * 2;
        ctx.beginPath();
        ctx.arc(cx, cy, R, a0, a1, false);
        ctx.strokeStyle = segColors[cls] || "rgba(255,255,255,0.06)";
        ctx.lineWidth = cls === "offDutyIdle" ? 20 : 52;
        ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(cx, cy, Rinner, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 2;
    ctx.stroke();
    for (let h = 0; h < 24; h++) {
        const a = -Math.PI / 2 + (h / 24) * Math.PI * 2;
        const long = h % 3 === 0;
        const r1 = long ? 272 : 284;
        ctx.beginPath();
        ctx.moveTo(cx + r1 * Math.cos(a), cy + r1 * Math.sin(a));
        ctx.lineTo(cx + R * Math.cos(a), cy + R * Math.sin(a));
        ctx.strokeStyle = long ? "rgba(255,255,255,0.35)" : "rgba(255,255,255,0.14)";
        ctx.lineWidth = long ? 4 : 2;
        ctx.stroke();
    }
    for (const m of dailyData.checkin_marks || []) {
        const [hh, mi] = String(m.time || "00:00").split(":").map(Number);
        const a = -Math.PI / 2 + (((hh || 0) * 60 + (mi || 0)) / 1440) * Math.PI * 2;
        const color = String(m.action || "").includes("out") ? "#f87171" : "#34d399";
        ctx.beginPath();
        ctx.moveTo(cx + 48 * Math.cos(a), cy + 48 * Math.sin(a));
        ctx.lineTo(cx + (Rinner - 10) * Math.cos(a), cy + (Rinner - 10) * Math.sin(a));
        ctx.strokeStyle = color;
        ctx.lineWidth = 10;
        ctx.lineCap = "round";
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx + (Rinner - 10) * Math.cos(a), cy + (Rinner - 10) * Math.sin(a), 10, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
    }
    ctx.lineCap = "butt";
    const eff = Number(dailyData.effective_minutes || 0) / 60;
    const focus = Number(dailyData.focus_ratio || 0).toFixed(0);
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(255,255,255,0.45)";
    ctx.font = exportFont(500, 12 * S);
    ctx.fillText("今日有效工时", cx, 420);
    ctx.fillStyle = "#ffffff";
    ctx.font = exportFont(800, 44 * S);
    ctx.fillText(eff.toFixed(1), cx, 456);
    ctx.fillStyle = "rgba(255,255,255,0.45)";
    ctx.font = exportFont(500, 12 * S);
    ctx.fillText("hours", cx, 556);
    ctx.fillStyle = "#34d399";
    ctx.font = exportFont(600, 12 * S);
    ctx.fillText(`专注率 ${focus}%`, cx, 590);
    ctx.textAlign = "left";

    // 图例
    const legend = [
        ["#34d399", "专注"],
        ["#fbbf24", "摸鱼"],
        ["#60a5fa", "收尾"],
        ["rgba(255,255,255,0.14)", "离岗"],
    ];
    const totalW = legend.length * 150 - 40;
    let lx = cx - totalW / 2;
    legend.forEach(([c, t]) => {
        ctx.fillStyle = c;
        ctx.beginPath();
        ctx.arc(lx + 8, 990, 8, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "rgba(255,255,255,0.5)";
        ctx.font = exportFont(500, 11 * S);
        ctx.fillText(t, lx + 26, 979);
        lx += 150;
    });

    // 底部统计
    const ratings = dailyData.ratings || [];
    const avg = ratings.length ? (ratings.reduce((s, r) => s + Number(r.value || 0), 0) / ratings.length).toFixed(1) : "-";
    const onDutyH = (Number(dailyData.on_duty_minutes || 0) / 60).toFixed(1);
    const activeH = (Number(dailyData.active_minutes || 0) / 60).toFixed(1);
    const slackMin = Math.max(0, Number(dailyData.on_duty_minutes || 0) - Number(dailyData.effective_minutes || 0));
    const stats = [
        { v: `${onDutyH}h`, l: "在岗" },
        { v: `${activeH}h`, l: "活跃" },
        { v: `${avg} ★`, l: `自评 ×${ratings.length}` },
        { v: `${slackMin}m`, l: "摸鱼" },
    ];
    const statsY = 1240;
    ctx.strokeStyle = "rgba(255,255,255,0.07)";
    ctx.beginPath();
    ctx.moveTo(64, 1220);
    ctx.lineTo(W - 64, 1220);
    ctx.stroke();
    stats.forEach((s, i) => {
        const x = W / 2 + (i - 1.5) * 200;
        ctx.textAlign = "center";
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(800, 20 * S);
        ctx.fillText(s.v, x, statsY);
        ctx.fillStyle = "rgba(255,255,255,0.35)";
        ctx.font = exportFont(500, 10 * S);
        ctx.fillText(s.l, x, statsY + 42);
    });
    ctx.textAlign = "left";
    ctx.fillStyle = "rgba(255,255,255,0.2)";
    ctx.font = exportFont(500, 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("TIME LEAVES ITS TRACE · 时间有迹可循", W / 2, H - 80);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}


// ── J · 复古票据（日图）────────────────────────────────
async function composeDailyReceiptBlob(dailyData) {
    const S = 2, W = 480 * S, H = 780 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    ctx.fillStyle = "#fbfaf5";
    ctx.fillRect(0, 0, W, H);
    // 上下边缘打孔（大孔、有间隙）；左右侧边直切
    ctx.fillStyle = "#0c0d12";
    for (let x = 0; x < W; x += 28 * S) {
        ctx.beginPath();
        ctx.arc(x + 9 * S, 0, 9 * S, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x + 9 * S, H, 9 * S, 0, Math.PI * 2);
        ctx.fill();
    }
    // 顶部撕口虚线
    ctx.strokeStyle = "#c9c6ba";
    ctx.lineWidth = 2 * S;
    ctx.setLineDash([10 * S, 8 * S]);
    ctx.beginPath();
    ctx.moveTo(56 * S, 26 * S);
    ctx.lineTo(W - 56 * S, 26 * S);
    ctx.stroke();
    ctx.setLineDash([]);

    const dateStr = dailyData.date || fmtDate(new Date());
    const weekday = WEEKDAY_CN[new Date(`${dateStr}T00:00:00`).getDay()] || "";
    const MONO = "'Courier New','SimSun','FangSong',serif"; // 西文打字机 + 中文宋体/仿宋（复古）
    const ML = 88 * S, MR = W - 88 * S; // 文字版心（窄、大留白）
    const RX_L = 56 * S, RX_R = W - 56 * S; // 分割横线用更宽的边距

    const dashedRule = (ry) => {
        ctx.strokeStyle = "#cfccc0";
        ctx.lineWidth = 1 * S;
        ctx.setLineDash([4 * S, 5 * S]);
        ctx.beginPath();
        ctx.moveTo(RX_L, ry);
        ctx.lineTo(RX_R, ry);
        ctx.stroke();
        ctx.setLineDash([]);
    };
    const solidRule = (ry, lw) => {
        ctx.strokeStyle = "#1c1c1c";
        ctx.lineWidth = lw * S;
        ctx.beginPath();
        ctx.moveTo(RX_L, ry);
        ctx.lineTo(RX_R, ry);
        ctx.stroke();
    };

    // 抬头
    ctx.textAlign = "center";
    ctx.fillStyle = "#1c1c1c";
    ctx.font = `700 ${20 * S}px ${MONO}`;
    ctx.fillText("FLOWTRACE 工时小票", W / 2, 56 * S);
    ctx.fillStyle = "#777777";
    ctx.font = `500 ${11 * S}px ${MONO}`;
    ctx.fillText(`NO. ${String(dateStr).replace(/-/g, "")}-001 · ${weekday}`, W / 2, 92 * S);
    ctx.textAlign = "left";
    solidRule(118 * S, 3);

    // 明细行
    const onDutyH = (Number(dailyData.on_duty_minutes || 0) / 60).toFixed(1);
    const effH = Number(dailyData.effective_minutes || 0) / 60;
    const activeH = (Number(dailyData.active_minutes || 0) / 60).toFixed(1);
    const slackMin = Math.max(0, Number(dailyData.on_duty_minutes || 0) - Number(dailyData.effective_minutes || 0));
    const ratings = dailyData.ratings || [];
    const avg = ratings.length ? (ratings.reduce((s, r) => s + Number(r.value || 0), 0) / ratings.length).toFixed(1) : "-";
    const rows = [
        ["在岗时长", `${onDutyH} h`],
        ["有效专注", `${effH.toFixed(1)} h (${Number(dailyData.focus_ratio || 0).toFixed(0)}%)`],
        ["活跃时长", `${activeH} h`],
        ["摸鱼时段", `${(slackMin / 60).toFixed(2)} h`],
        ["状态自评", `${avg} ★ × ${ratings.length}`],
    ];
    let y = 150 * S;
    rows.forEach(([k, v]) => {
        ctx.fillStyle = "#666666";
        ctx.font = `500 ${13 * S}px ${MONO}`;
        ctx.fillText(k, ML, y);
        ctx.fillStyle = k === "有效专注" ? "#0b7a3b" : "#1c1c1c";
        ctx.font = `700 ${13 * S}px ${MONO}`;
        ctx.textAlign = "right";
        ctx.fillText(v, MR, y);
        ctx.textAlign = "left";
        y += 32 * S;
    });
    dashedRule(310 * S);

    // 打卡记录（首条 + 末条，中间折叠灰字居中）
    ctx.fillStyle = "#a3322a";
    ctx.font = `700 ${11 * S}px ${MONO}`;
    ctx.fillText("— 打卡记录 —", ML, 330 * S);
    const marks = (dailyData.checkin_marks || [])
        .slice()
        .sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
    let cy = 356 * S;
    const drawMarkRow = (m) => {
        const isIn = !String(m.action || "").includes("out");
        ctx.fillStyle = "#888888";
        ctx.font = `500 ${12 * S}px ${MONO}`;
        ctx.fillText(m.time || "--:--", ML, cy);
        const markText = checkinText(m.action, !!m.anomaly, !!m.inferred, !!m.current);
        ctx.fillStyle = m.current ? "#b8860b" : isIn ? "#0b7a3b" : "#a3322a";
        ctx.font = `700 ${12 * S}px ${MONO}`;
        ctx.textAlign = "right";
        ctx.fillText((m.current ? "" : isIn ? "▼ " : "▲ ") + markText, MR, cy);
        ctx.textAlign = "left";
        cy += 24 * S;
    };
    const drawFoldLine = (text) => {
        ctx.fillStyle = "#999999";
        ctx.font = `500 ${11 * S}px ${MONO}`;
        ctx.textAlign = "center";
        ctx.fillText(text, W / 2, cy);
        ctx.textAlign = "left";
        cy += 24 * S;
    };
    if (!marks.length) {
        drawFoldLine("— 今日暂无打卡 —");
    } else {
        drawMarkRow(marks[0]);
        if (marks.length > 2) {
            const middle = marks.slice(1, -1);
            const times = middle.map((m) => m.time || "--:--").join(" · ");
            drawFoldLine(middle.length <= 4 ? `… ${times} …` : `… 还有 ${middle.length} 次打卡 …`);
        }
        if (marks.length > 1) drawMarkRow(marks[marks.length - 1]);
    }
    dashedRule(cy + 14 * S);

    // 合计（双实线夹行，随打卡区流式下移）
    const totalY = cy + 36 * S;
    solidRule(totalY, 3);
    ctx.fillStyle = "#1c1c1c";
    ctx.font = `700 ${13 * S}px ${MONO}`;
    ctx.fillText("今日合计", ML, totalY + 18 * S);
    ctx.fillStyle = "#0b7a3b";
    ctx.font = `700 ${14 * S}px ${MONO}`;
    ctx.textAlign = "right";
    ctx.fillText(`${effH.toFixed(1)}h 有效专注`, MR, totalY + 18 * S);
    ctx.textAlign = "left";
    solidRule(totalY + 48 * S, 1);

    // 工作日程条码（24 格 × 1h）：等高条，粗 = 活跃分钟，色 = 主导状态，黑 = 没工作
    ctx.fillStyle = "#a3322a";
    ctx.font = `700 ${11 * S}px ${MONO}`;
    ctx.fillText("— 工作日程（每格 1h）—", ML, totalY + 76 * S);
    const stripY = totalY + 104 * S, stripH = 48 * S;
    const SCH_COLORS = { activeOnDuty: "#0b7a3b", onDutyIdle: "#b8860b", activeOffDuty: "#4a7dbd", offDutyIdle: "#1c1c1c" };
    const segs = dailyData.timeline_segments || [];
    const barW = [], barC = [];
    for (let h = 0; h < 24; h++) {
        const hs = h * 60, he = hs + 60, acc = {};
        for (const sg of segs) {
            const lo = Math.max(Number(sg.start_min || 0), hs);
            const hi = Math.min(Number(sg.end_min || 0), he);
            if (hi <= lo) continue;
            const cls = exportSegmentClass(sg);
            acc[cls] = (acc[cls] || 0) + (hi - lo);
        }
        const act = (acc.activeOnDuty || 0) + (acc.activeOffDuty || 0);
        let dom = "offDutyIdle", best = 0;
        for (const k in acc) {
            if (acc[k] > best) { best = acc[k]; dom = k; }
        }
        barW.push((2 + Math.round((act / 60) * 8)) * S);
        barC.push(SCH_COLORS[dom] || SCH_COLORS.offDutyIdle);
    }
    // 等间距连续排布：条间距固定、与粗细无关；整体居中，宽度由数据决定
    const gapW = 4 * S;
    const totalW = barW.reduce((a, b) => a + b, 0) + gapW * 23;
    const centers = [];
    let curX = (W - totalW) / 2;
    for (let h = 0; h < 24; h++) {
        curX += barW[h] / 2;
        centers.push(curX);
        ctx.fillStyle = barC[h];
        ctx.fillRect(curX - barW[h] / 2, stripY, barW[h], stripH);
        curX += barW[h] / 2 + gapW;
    }
    const stripRight = curX - gapW;
    // 打卡标记（首/尾 = 箭头指向条带；中间 = 下沿圆点）
    marks.forEach((m, idx) => {
        const [hh, mi] = String(m.time || "00:00").split(":").map(Number);
        const fh = ((hh || 0) * 60 + (mi || 0)) / 60;
        if (fh < 0 || fh > 24) return;
        const cx = centers[Math.min(23, Math.max(0, Math.floor(fh)))];
        ctx.fillStyle = "#1c1c1c";
        if (idx !== 0 && idx !== marks.length - 1) {
            ctx.beginPath();
            ctx.arc(cx, stripY + stripH + 5 * S, 2 * S, 0, Math.PI * 2);
            ctx.fill();
            return;
        }
        const isOut = String(m.action || "").includes("out");
        ctx.beginPath();
        if (isOut) {
            // 下沿 ▲ 向上指向条带
            ctx.moveTo(cx - 4 * S, stripY + stripH + 9 * S);
            ctx.lineTo(cx + 4 * S, stripY + stripH + 9 * S);
            ctx.lineTo(cx, stripY + stripH + 2 * S);
        } else {
            // 上沿 ▼ 向下指向条带
            ctx.moveTo(cx - 4 * S, stripY - 9 * S);
            ctx.lineTo(cx + 4 * S, stripY - 9 * S);
            ctx.lineTo(cx, stripY - 2 * S);
        }
        ctx.closePath();
        ctx.fill();
    });
    // 刻度（钉在对应小时的条码中心）
    const tickY = stripY + stripH + 14 * S;
    ctx.fillStyle = "#999999";
    ctx.font = `500 ${9 * S}px ${MONO}`;
    [[0, "00"], [6, "06"], [12, "12"], [18, "18"], [24, "24"]].forEach(([hr, t]) => {
        ctx.textAlign = hr === 24 ? "right" : "center";
        ctx.fillText(t, hr === 24 ? stripRight : centers[hr], tickY);
    });
    ctx.textAlign = "left";
    // 图例
    const legend = [["在岗活跃", SCH_COLORS.activeOnDuty], ["闲置", SCH_COLORS.onDutyIdle], ["下班活跃", SCH_COLORS.activeOffDuty], ["未工作", SCH_COLORS.offDutyIdle]];
    const lgY = tickY + 18 * S;
    ctx.font = `500 ${8 * S}px ${MONO}`;
    ctx.textAlign = "right";
    let lgX = stripRight;
    for (let i = legend.length - 1; i >= 0; i--) {
        const [label, color] = legend[i];
        const tw = ctx.measureText(label).width;
        ctx.fillStyle = "#888888";
        ctx.fillText(label, lgX, lgY);
        const swX = lgX - tw - 11 * S;
        ctx.fillStyle = color;
        ctx.fillRect(swX, lgY + 1 * S, 7 * S, 7 * S);
        lgX = swX - 9 * S;
    }
    ctx.textAlign = "left";

    // 页脚
    ctx.fillStyle = "#999999";
    ctx.font = `500 ${10 * S}px ${MONO}`;
    ctx.textAlign = "center";
    ctx.fillText("FLOWTRACE · 时间有迹可循 · 感谢记录每一天", W / 2, H - 44 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// 单日每小时活跃分钟 → 0-4 级强度（24 格）
function buildDayDensity(dailyData) {
    const activeMin = new Array(24).fill(0);
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        if (cls !== "activeOnDuty" && cls !== "activeOffDuty") continue;
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        for (let h = 0; h < 24; h++) {
            const hs = h * 60;
            const lo = Math.max(start, hs);
            const hi = Math.min(end, hs + 60);
            if (hi > lo) activeMin[h] += hi - lo;
        }
    }
    return activeMin.map((m) => (m >= 55 ? 4 : m >= 40 ? 3 : m >= 20 ? 2 : m >= 5 ? 1 : 0));
}

// ── A-3 · GitHub 单日强度条（日图）─────────────────────
async function composeDailyGithubBlob(dailyData) {
    const S = 2, W = 480 * S, H = 760 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    ctx.fillStyle = "#0d1117";
    ctx.fillRect(0, 0, W, H);
    const halo = ctx.createRadialGradient(W - 260, -120, 20, W - 260, -120, 420);
    halo.addColorStop(0, "rgba(63,185,80,0.10)");
    halo.addColorStop(1, "rgba(63,185,80,0)");
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, W, H);

    const dateStr = dailyData.date || fmtDate(new Date());
    const weekday = WEEKDAY_CN[new Date(`${dateStr}T00:00:00`).getDay()] || "";
    ctx.fillStyle = "#e6edf3";
    ctx.font = exportFont(700, 13 * S);
    ctx.fillText("FLOWTRACE · DAILY", 56 * S, 56);
    const rangeText = `${dateStr}（${weekday}）`;
    const bw = 13 * S * String(rangeText).length * 0.9 + 48 * S;
    drawRoundedRect(ctx, W - 56 * S - bw, 48, bw, 34 * S, 17 * S);
    ctx.fillStyle = "#161b22";
    ctx.fill();
    ctx.strokeStyle = "#21262d";
    ctx.lineWidth = 1 * S;
    ctx.stroke();
    ctx.fillStyle = "#8b949e";
    ctx.font = exportFont(500, 12 * S);
    ctx.fillText(rangeText, W - 56 * S - bw + 24 * S, 58);

    // hero
    const eff = Number(dailyData.effective_minutes || 0) / 60;
    const focus = Number(dailyData.focus_ratio || 0).toFixed(0);
    ctx.fillStyle = "#e6edf3";
    ctx.font = exportFont(800, 40 * S);
    ctx.fillText(eff.toFixed(1), 56 * S, 140);
    ctx.fillStyle = "#484f58";
    ctx.font = exportFont(500, 16 * S);
    ctx.fillText("小时有效专注", 56 * S + ctx.measureText(eff.toFixed(1)).width + 14 * S, 160);
    ctx.fillStyle = "#3fb950";
    ctx.font = exportFont(600, 13 * S);
    ctx.fillText(`专注率 ${focus}% · 状态自评 ${dailyData.ratings && dailyData.ratings.length ? (dailyData.ratings.reduce((s, r) => s + Number(r.value || 0), 0) / dailyData.ratings.length).toFixed(1) : "-"}`, 56 * S, 250);

    // 24h 强度条
    ctx.fillStyle = "#8b949e";
    ctx.font = exportFont(600, 11 * S);
    ctx.fillText("每小时活跃强度 · 24h", 56 * S, 300);
    const density = buildDayDensity(dailyData);
    const gColors = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"];
    const gridX = 56 * S, gridY = 340 * S, gap = 8 * S;
    const cellW = (W - 112 - gap * 23) / 24;
    density.forEach((lv, i) => {
        const x = gridX + i * (cellW + gap);
        drawRoundedRect(ctx, x, gridY, cellW, 120 * S, 3 * S);
        ctx.fillStyle = gColors[lv] || gColors[0];
        ctx.fill();
    });
    // 小时刻度
    ctx.fillStyle = "#30363d";
    ctx.font = exportFont(500, 9 * S);
    [0, 6, 12, 18, 24].forEach((h, i) => {
        const x = gridX + (h / 24) * (W - 112) - (i === 0 ? 0 : i === 4 ? 16 * S : 8 * S);
        ctx.fillText(String(h).padStart(2, "0"), x, gridY + 140 * S);
    });

    // 打卡徽章
    const marks = dailyData.checkin_marks || [];
    const markY = gridY + 150 * S, markH = 60 * S;
    const markW = (W - 112 - 20 * S) / 2;
    const labels = { clock_in: ["CLOCK IN", "#3fb950"], clock_out: ["CLOCK OUT", "#f85149"], in: ["CLOCK IN", "#3fb950"], out: ["CLOCK OUT", "#f85149"] };
    marks.slice(0, 2).forEach((m, i) => {
        const cx = gridX + i * (markW + 20 * S);
        const action = normalizeCheckinAction(m.action);
        const [t, c] = labels[action] || ["MARK", "#8b949e"];
        ctx.strokeStyle = c;
        ctx.lineWidth = 2 * S;
        drawRoundedRect(ctx, cx, markY, markW, markH, 10 * S);
        ctx.stroke();
        ctx.fillStyle = c;
        ctx.font = exportFont(700, 14 * S);
        ctx.fillText(m.time || "--:--", cx + 24 * S, markY + 12 * S);
        ctx.fillStyle = "#8b949e";
        ctx.font = exportFont(600, 11 * S);
        ctx.fillText(exportCheckinLabel(m, Object.fromEntries(Object.entries(labels).map(([key, value]) => [key, value[0]]))), cx + 24 * S, markY + 42 * S);
    });

    // 底部统计
    const statsY = markY + markH + 60 * S;
    const onDutyH = (Number(dailyData.on_duty_minutes || 0) / 60).toFixed(1);
    const activeH = (Number(dailyData.active_minutes || 0) / 60).toFixed(1);
    const slackMin = Math.max(0, Number(dailyData.on_duty_minutes || 0) - Number(dailyData.effective_minutes || 0));
    const stats = [
        { v: `${onDutyH}h`, l: "在岗" },
        { v: `${activeH}h`, l: "活跃" },
        { v: `${slackMin}m`, l: "摸鱼" },
    ];
    ctx.strokeStyle = "#21262d";
    ctx.lineWidth = 1 * S;
    ctx.beginPath();
    ctx.moveTo(56 * S, statsY - 12 * S);
    ctx.lineTo(W - 56 * S, statsY - 12 * S);
    ctx.stroke();
    stats.forEach((s, i) => {
        const x = W / 2 + (i - 1) * 150 * S;
        ctx.textAlign = "center";
        ctx.fillStyle = "#e6edf3";
        ctx.font = exportFont(700, 20 * S);
        ctx.fillText(s.v, x, statsY);
        ctx.fillStyle = "#484f58";
        ctx.font = exportFont(500, 11 * S);
        ctx.fillText(s.l, x, statsY + 40 * S);
    });
    ctx.textAlign = "left";
    ctx.fillStyle = "#30363d";
    ctx.font = exportFont(400, 11 * S);
    ctx.textAlign = "center";
    ctx.fillText("Flowtrace — 时间有迹可循", W / 2, H - 40 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── B-3 · Wrapped 胶囊时间线（日图）────────────────────
async function composeDailyWrappedBlob(dailyData) {
    const S = 2, W = 480 * S, H = 760 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    const bg = ctx.createLinearGradient(0, 0, W * 0.35, H);
    bg.addColorStop(0, "#141b3d");
    bg.addColorStop(0.45, "#16213e");
    bg.addColorStop(1, "#0a1628");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    let g = ctx.createRadialGradient(W, H, 20, W, H, 300 * S);
    g.addColorStop(0, "rgba(139,92,246,0.18)");
    g.addColorStop(1, "rgba(139,92,246,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    const dateStr = dailyData.date || fmtDate(new Date());
    const weekday = WEEKDAY_CN[new Date(`${dateStr}T00:00:00`).getDay()] || "";
    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.font = exportFont(700, 12 * S);
    ctx.fillText("FLOWTRACE", 64, 72);
    const rangeText = `${weekday} · ${String(dateStr).slice(5).replace("-", ".")}`;
    const bw = 12 * S * String(rangeText).length * 0.85 + 40 * S;
    drawRoundedRect(ctx, W - 64 - bw, 64, bw, 34 * S, 17 * S);
    ctx.fillStyle = "rgba(255,255,255,0.06)";
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.3)";
    ctx.font = exportFont(500, 11 * S);
    ctx.fillText(rangeText, W - 64 - bw + 20 * S, 72);

    // hero
    const ratings = dailyData.ratings || [];
    const avg = ratings.length ? (ratings.reduce((s, r) => s + Number(r.value || 0), 0) / ratings.length).toFixed(1) : "-";
    const eff = Number(dailyData.effective_minutes || 0) / 60;
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    ctx.font = exportFont(500, 13 * S);
    ctx.fillText("今日有效工时", W / 2, 180);
    const numY = 220;
    const grad = ctx.createLinearGradient(0, numY, 0, numY + 170 * S);
    grad.addColorStop(0, "#ffffff");
    grad.addColorStop(1, "rgba(255,255,255,0.45)");
    ctx.fillStyle = grad;
    ctx.font = exportFont(900, 88 * S);
    ctx.fillText(eff.toFixed(1), W / 2, numY);
    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.font = exportFont(500, 18 * S);
    ctx.fillText("hours of focus", W / 2, numY + 180 * S);
    const rt = `状态自评 ${avg} ★ · 专注率 ${Number(dailyData.focus_ratio || 0).toFixed(0)}%`;
    const rw = 14 * S * String(rt).length * 0.95 + 56 * S;
    drawRoundedRect(ctx, W / 2 - rw / 2, numY + 230 * S, rw, 40 * S, 20 * S);
    ctx.fillStyle = "rgba(29,185,84,0.1)";
    ctx.fill();
    ctx.fillStyle = "#1db954";
    ctx.font = exportFont(600, 14 * S);
    ctx.fillText(rt, W / 2, numY + 238 * S);
    ctx.textAlign = "left";

    // 胶囊时间线
    const tlX = 64 * S, tlW = W - 128 * S, tlY = numY + 330 * S, tlH = 60 * S;
    drawRoundedRect(ctx, tlX, tlY, tlW, tlH, 30 * S);
    ctx.fillStyle = "rgba(255,255,255,0.05)";
    ctx.fill();
    const segColors = {
        activeOnDuty: "#1db954",
        onDutyIdle: "#f59e0b",
        activeOffDuty: "#818cf8",
        offDutyIdle: "rgba(255,255,255,0.06)",
    };
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        if (end <= start) continue;
        const x = tlX + (start / 1440) * tlW;
        const w = ((end - start) / 1440) * tlW;
        ctx.fillStyle = segColors[cls] || "rgba(255,255,255,0.06)";
        drawRoundedRect(ctx, x + 2 * S, tlY + 6 * S, Math.max(w - 4 * S, 2 * S), tlH - 12 * S, (tlH - 12 * S) / 2);
        ctx.fill();
    }
    ctx.fillStyle = "rgba(255,255,255,0.25)";
    ctx.font = exportFont(500, 10 * S);
    ctx.textAlign = "left";
    ctx.fillText("00", tlX, tlY + tlH + 18 * S);
    ctx.textAlign = "center";
    ctx.fillText("06", tlX + tlW * 0.25, tlY + tlH + 18 * S);
    ctx.fillText("12", tlX + tlW * 0.5, tlY + tlH + 18 * S);
    ctx.fillText("18", tlX + tlW * 0.75, tlY + tlH + 18 * S);
    ctx.textAlign = "right";
    ctx.fillText("24", tlX + tlW, tlY + tlH + 18 * S);
    ctx.textAlign = "left";

    // 打卡芯片
    const marks = dailyData.checkin_marks || [];
    const chipY = tlY + tlH + 40 * S, chipH = 80 * S;
    const chipW = (tlW - 20 * S) / 2;
    const labels = { clock_in: "CLOCK IN", clock_out: "CLOCK OUT", in: "CLOCK IN", out: "CLOCK OUT" };
    marks.slice(0, 2).forEach((m, i) => {
        const cx = tlX + i * (chipW + 20 * S);
        drawRoundedRect(ctx, cx, chipY, chipW, chipH, 12 * S);
        ctx.fillStyle = "rgba(255,255,255,0.05)";
        ctx.fill();
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(800, 20 * S);
        ctx.textAlign = "center";
        ctx.fillText(m.time || "--:--", cx + chipW / 2, chipY + 16 * S);
        ctx.fillStyle = "rgba(255,255,255,0.35)";
        ctx.font = exportFont(600, 10 * S);
        const markLabel = exportCheckinLabel(m, labels);
        ctx.fillText(markLabel, cx + chipW / 2, chipY + 54 * S);
        ctx.textAlign = "left";
    });

    // 底部统计
    const statsY = chipY + chipH + 60 * S;
    const onDutyH = (Number(dailyData.on_duty_minutes || 0) / 60).toFixed(1);
    const activeH = (Number(dailyData.active_minutes || 0) / 60).toFixed(1);
    const slackMin = Math.max(0, Number(dailyData.on_duty_minutes || 0) - Number(dailyData.effective_minutes || 0));
    const stats = [
        { v: `${onDutyH}h`, l: "在岗" },
        { v: `${activeH}h`, l: "活跃" },
        { v: `${slackMin}m`, l: "摸鱼" },
    ];
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1 * S;
    ctx.beginPath();
    ctx.moveTo(64 * S, statsY - 14 * S);
    ctx.lineTo(W - 64 * S, statsY - 14 * S);
    ctx.stroke();
    stats.forEach((s, i) => {
        const x = W / 2 + (i - 1) * 150 * S;
        ctx.textAlign = "center";
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(800, 20 * S);
        ctx.fillText(s.v, x, statsY);
        ctx.fillStyle = "rgba(255,255,255,0.3)";
        ctx.font = exportFont(500, 11 * S);
        ctx.fillText(s.l, x, statsY + 40 * S);
    });
    ctx.textAlign = "left";
    ctx.fillStyle = "rgba(255,255,255,0.15)";
    ctx.font = exportFont(400, 11 * S);
    ctx.textAlign = "center";
    ctx.fillText("时间有迹可循", W / 2, H - 30 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── C-3 · Fitness 三环（日图）──────────────────────────
async function composeDailyFitnessBlob(dailyData) {
    const S = 2, W = 480 * S, H = 680 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    ctx.fillStyle = "#fafafa";
    ctx.fillRect(0, 0, W, H);
    // accent bar
    const accent = ctx.createLinearGradient(0, 0, W, 0);
    accent.addColorStop(0, "#34d399");
    accent.addColorStop(0.5, "#3b82f6");
    accent.addColorStop(1, "#8b5cf6");
    ctx.fillStyle = accent;
    ctx.fillRect(0, 0, W, 8 * S);

    // 头部
    const dateStr = dailyData.date || fmtDate(new Date());
    ctx.fillStyle = "#111111";
    ctx.font = exportFont(700, 14 * S);
    ctx.fillText("Flowtrace · 今日总结", 40 * S, 64);
    ctx.fillStyle = "#888888";
    ctx.font = exportFont(500, 12 * S);
    ctx.textAlign = "right";
    ctx.fillText(String(dateStr).slice(5).replace("-", "/"), W - 40 * S, 66);
    ctx.textAlign = "left";

    // 三环（比例由当日数据动态计算）
    const onDutyMin = Number(dailyData.on_duty_minutes || 0);
    const effMin = Number(dailyData.effective_minutes || 0);
    const activeMin = Number(dailyData.active_minutes || 0);
    const cx = 180 * S, cy = 215 * S;
    const rings = [
        { r: 100 * S, pct: Math.min(1, onDutyMin / 600), color: "#34d399" },   // 在岗，目标 10h
        { r: 77 * S, pct: Math.min(1, effMin / 525), color: "#3b82f6" },       // 有效，目标 8.75h
        { r: 53 * S, pct: Math.min(1, activeMin / 480), color: "#8b5cf6" },    // 活跃，目标 8h
    ];
    rings.forEach((ring) => {
        ctx.beginPath();
        ctx.arc(cx, cy, ring.r, 0, Math.PI * 2);
        ctx.strokeStyle = "#e8e8e8";
        ctx.lineWidth = 18 * S;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx, cy, ring.r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * ring.pct, false);
        ctx.strokeStyle = ring.color;
        ctx.lineWidth = 18 * S;
        ctx.lineCap = "round";
        ctx.stroke();
        ctx.lineCap = "butt";
    });
    const eff = effMin / 60;
    ctx.textAlign = "center";
    ctx.fillStyle = "#111111";
    ctx.font = exportFont(800, 30 * S);
    ctx.fillText(eff.toFixed(1), cx, cy - 40 * S);
    ctx.fillStyle = "#999999";
    ctx.font = exportFont(500, 12 * S);
    ctx.fillText("有效小时", cx, cy + 8 * S);
    ctx.textAlign = "left";

    // 右侧 legend
    const onDutyH = onDutyMin / 60;
    const activeH = activeMin / 60;
    const legends = [
        ["#34d399", "在岗", `${onDutyH.toFixed(1)}h`],
        ["#3b82f6", "有效", `${eff.toFixed(1)}h`],
        ["#8b5cf6", "活跃", `${activeH.toFixed(1)}h`],
    ];
    legends.forEach(([c, t, v], i) => {
        const y = 165 * S + i * 52 * S;
        ctx.fillStyle = c;
        ctx.beginPath();
        ctx.arc(cx + 130 * S, y + 10 * S, 10 * S, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "#555555";
        ctx.font = exportFont(500, 13 * S);
        ctx.fillText(t, cx + 152 * S, y);
        ctx.fillStyle = "#111111";
        ctx.font = exportFont(700, 13 * S);
        ctx.fillText(v, cx + 152 * S + ctx.measureText(t).width + 24 * S, y);
    });

    // 24h 时间轴
    const tlX = 40 * S, tlW = W - 80 * S, tlY = 350 * S, tlH = 50 * S;
    drawRoundedRect(ctx, tlX, tlY, tlW, tlH, 8 * S);
    ctx.fillStyle = "#f0f0f0";
    ctx.fill();
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        if (end <= start) continue;
        const x = tlX + (start / 1440) * tlW;
        const w = ((end - start) / 1440) * tlW;
        ctx.fillStyle = cls === "activeOnDuty" || cls === "activeOffDuty" ? "#3b82f6" : cls === "onDutyIdle" ? "#dddddd" : "#e8e8e8";
        ctx.fillRect(x, tlY + 4 * S, Math.max(w, 1 * S), tlH - 8 * S);
    }
    for (const m of dailyData.checkin_marks || []) {
        const [hh, mi] = String(m.time || "00:00").split(":").map(Number);
        const x = tlX + (((hh || 0) * 60 + (mi || 0)) / 1440) * tlW;
        ctx.fillStyle = "#ff3b30";
        ctx.fillRect(x - 1.5 * S, tlY - 6 * S, 3 * S, tlH + 12 * S);
    }
    ctx.fillStyle = "#bbbbbb";
    ctx.font = exportFont(500, 10 * S);
    ctx.textAlign = "left";
    ctx.fillText("00", tlX, tlY + tlH + 16 * S);
    ctx.textAlign = "center";
    ctx.fillText("06", tlX + tlW * 0.25, tlY + tlH + 16 * S);
    ctx.fillText("12", tlX + tlW * 0.5, tlY + tlH + 16 * S);
    ctx.fillText("18", tlX + tlW * 0.75, tlY + tlH + 16 * S);
    ctx.textAlign = "right";
    ctx.fillText("24", tlX + tlW, tlY + tlH + 16 * S);
    ctx.textAlign = "left";

    // 底部统计
    const statsY = tlY + tlH + 80 * S;
    const ratings = dailyData.ratings || [];
    const avg = ratings.length ? (ratings.reduce((s, r) => s + Number(r.value || 0), 0) / ratings.length).toFixed(1) : "-";
    const slackMin = Math.max(0, onDutyMin - effMin);
    const stats = [
        { v: `${Number(dailyData.focus_ratio || 0).toFixed(0)}%`, l: "专注率" },
        { v: `${avg}`, l: "自评" },
        { v: `${slackMin}m`, l: "摸鱼" },
    ];
    stats.forEach((s, i) => {
        const x = W / 2 + (i - 1) * 150 * S;
        ctx.textAlign = "center";
        ctx.fillStyle = "#111111";
        ctx.font = exportFont(800, 18 * S);
        ctx.fillText(s.v, x, statsY);
        ctx.fillStyle = "#aaaaaa";
        ctx.font = exportFont(500, 10 * S);
        ctx.fillText(s.l, x, statsY + 32 * S);
    });
    ctx.textAlign = "left";
    const marks = dailyData.checkin_marks || [];
    ctx.fillStyle = "#999999";
    ctx.font = exportFont(500, 11 * S);
    ctx.textAlign = "center";
    ctx.fillText(`${marks[0] ? marks[0].time : "--:--"} — ${marks[1] ? marks[1].time : "--:--"} · Flowtrace 时间有迹可循`, W / 2, H - 45 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── F-4 · 杂志冷色版（日图）────────────────────────────
async function composeDailyMagazineCoolBlob(dailyData) {
    const S = 2, W = 480 * S, H = 780 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    const bg = ctx.createLinearGradient(0, 0, W * 0.4, H);
    bg.addColorStop(0, "#1f6df0");
    bg.addColorStop(0.55, "#6a3df2");
    bg.addColorStop(1, "#0e1a3a");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    let g = ctx.createRadialGradient(W, 0, 20, W, 0, 340 * S);
    g.addColorStop(0, "rgba(255,255,255,0.16)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    const dateStr = dailyData.date || fmtDate(new Date());
    const weekday = WEEKDAY_CN[new Date(`${dateStr}T00:00:00`).getDay()] || "";
    const dayNum = String(dateStr).slice(8);
    const rangeText = `${weekday} · ${String(dateStr).slice(5).replace("-", ".")}`;
    ctx.fillStyle = "#ffffff";
    ctx.font = exportFont(700, 12 * S);
    ctx.fillText("FLOWTRACE", 52 * S, 64);
    const bw = 11 * S * String(rangeText).length * 0.9 + 48 * S;
    drawRoundedRect(ctx, W - 52 * S - bw, 56, bw, 36 * S, 18 * S);
    ctx.strokeStyle = "rgba(255,255,255,0.55)";
    ctx.lineWidth = 1.5 * S;
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    ctx.font = exportFont(700, 11 * S);
    ctx.fillText(rangeText, W - 52 * S - bw + 24 * S, 64);

    // hero
    const ratings = dailyData.ratings || [];
    const avg = ratings.length ? (ratings.reduce((s, r) => s + Number(r.value || 0), 0) / ratings.length).toFixed(1) : "-";
    const eff = Number(dailyData.effective_minutes || 0) / 60;
    const focus = Number(dailyData.focus_ratio || 0).toFixed(0);
    ctx.fillStyle = "rgba(255,255,255,0.75)";
    ctx.font = exportFont(700, 13 * S);
    ctx.fillText(`DAY ${dayNum} · COOL EDITION`, 52 * S, 170);
    ctx.fillStyle = "#ffffff";
    ctx.shadowColor = "rgba(0,0,0,0.28)";
    ctx.shadowBlur = 28 * S;
    ctx.font = exportFont(900, 118 * S);
    ctx.fillText(eff.toFixed(1), 52 * S, 220);
    ctx.shadowBlur = 0;
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.font = exportFont(800, 26 * S);
    ctx.fillText("FOCUS HOURS TODAY", 52 * S, 480);
    const subTxt = `状态自评 ${avg} ★ · 专注率 ${focus}%`;
    const sw = 13 * S * String(subTxt).length * 1.0 + 56 * S;
    ctx.save();
    ctx.translate(52 * S, 560);
    ctx.rotate(-1.5 * Math.PI / 180);
    drawRoundedRect(ctx, 0, 0, sw, 52 * S, 26 * S);
    ctx.fillStyle = "#ffd640";
    ctx.fill();
    ctx.fillStyle = "#3a1f00";
    ctx.font = exportFont(700, 12.5 * S);
    ctx.fillText(subTxt, 28 * S, 15 * S);
    ctx.restore();

    // 时间轴（冷色分段）
    const segColors = {
        activeOnDuty: "#67e8f9",
        onDutyIdle: "#ffffff",
        activeOffDuty: "#a78bfa",
        offDutyIdle: "rgba(255,255,255,0.15)",
    };
    const tlX = 52 * S, tlW = W - 104 * S, tlY = 340 * S, tlH = 104 * S;
    drawRoundedRect(ctx, tlX, tlY, tlW, tlH, 12 * S);
    ctx.fillStyle = "rgba(255,255,255,0.15)";
    ctx.fill();
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        if (end <= start) continue;
        const x = tlX + (start / 1440) * tlW;
        const w = ((end - start) / 1440) * tlW;
        ctx.fillStyle = segColors[cls] || "rgba(255,255,255,0.15)";
        ctx.fillRect(x, tlY + 4 * S, Math.max(w, 1 * S), tlH - 8 * S);
    }
    ctx.fillStyle = "rgba(255,255,255,0.6)";
    ctx.font = exportFont(700, 10 * S);
    ctx.textAlign = "left";
    ctx.fillText("00", tlX, tlY + tlH + 18 * S);
    ctx.textAlign = "center";
    ctx.fillText("06", tlX + tlW * 0.25, tlY + tlH + 18 * S);
    ctx.fillText("12", tlX + tlW * 0.5, tlY + tlH + 18 * S);
    ctx.fillText("18", tlX + tlW * 0.75, tlY + tlH + 18 * S);
    ctx.textAlign = "right";
    ctx.fillText("24", tlX + tlW, tlY + tlH + 18 * S);
    ctx.textAlign = "left";

    // 打卡芯片
    const marks = dailyData.checkin_marks || [];
    const chipY = tlY + tlH + 46 * S, chipH = 80 * S;
    const chipW = (tlW - 20 * S) / 2;
    const markLabels = { clock_in: "CLOCK IN", clock_out: "CLOCK OUT", in: "CLOCK IN", out: "CLOCK OUT" };
    marks.slice(0, 2).forEach((m, i) => {
        const cx = tlX + i * (chipW + 20 * S);
        drawRoundedRect(ctx, cx, chipY, chipW, chipH, 10 * S);
        ctx.fillStyle = "rgba(255,255,255,0.14)";
        ctx.fill();
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(900, 16 * S);
        ctx.textAlign = "center";
        ctx.fillText(m.time || "--:--", cx + chipW / 2, chipY + 18 * S);
        ctx.fillStyle = "rgba(255,255,255,0.7)";
        ctx.font = exportFont(700, 9.5 * S);
        const markLabel = exportCheckinLabel(m, markLabels);
        ctx.fillText(markLabel, cx + chipW / 2, chipY + 52 * S);
        ctx.textAlign = "left";
    });

    // 底部统计
    const statsY = chipY + chipH + 40 * S;
    const onDutyH = (Number(dailyData.on_duty_minutes || 0) / 60).toFixed(1);
    const activeH = (Number(dailyData.active_minutes || 0) / 60).toFixed(1);
    const slackMin = Math.max(0, Number(dailyData.on_duty_minutes || 0) - Number(dailyData.effective_minutes || 0));
    const stats = [
        { v: `${onDutyH}h`, l: "在岗" },
        { v: `${activeH}h`, l: "活跃" },
        { v: `${slackMin}m`, l: "摸鱼" },
    ];
    const cardW = (tlW - 2 * 20 * S) / 3;
    stats.forEach((s, i) => {
        const cx = tlX + i * (cardW + 20 * S);
        drawRoundedRect(ctx, cx, statsY, cardW, 110 * S, 14 * S);
        ctx.fillStyle = "rgba(255,255,255,0.14)";
        ctx.fill();
        ctx.strokeStyle = "rgba(255,255,255,0.22)";
        ctx.lineWidth = 1 * S;
        ctx.stroke();
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(900, 19 * S);
        ctx.textAlign = "center";
        ctx.fillText(s.v, cx + cardW / 2, statsY + 20 * S);
        ctx.fillStyle = "rgba(255,255,255,0.75)";
        ctx.font = exportFont(600, 9.5 * S);
        ctx.fillText(s.l, cx + cardW / 2, statsY + 62 * S);
        ctx.textAlign = "left";
    });
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.font = exportFont(600, 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("FOCUS EDITION · 时间有迹可循", W / 2, H - 48 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── F-5 · 头条新闻版（日图）────────────────────────────
async function composeDailyHeadlineBlob(dailyData) {
    const S = 2, W = 480 * S, H = 780 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    const bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#0f172a");
    bg.addColorStop(1, "#1e293b");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);

    const dateStr = dailyData.date || fmtDate(new Date());
    const weekday = WEEKDAY_CN[new Date(`${dateStr}T00:00:00`).getDay()] || "";
    // 头条标签
    ctx.fillStyle = "#e63946";
    ctx.fillRect(0, 0, W, 10 * S);
    ctx.fillStyle = "#e63946";
    ctx.font = exportFont(800, 11 * S);
    ctx.fillText("EXCLUSIVE", 52 * S, 64);
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.font = exportFont(500, 11 * S);
    ctx.textAlign = "right";
    ctx.fillText(`${weekday} · ${dateStr}`, W - 52 * S, 66);
    ctx.textAlign = "left";

    // 大标题
    const eff = Number(dailyData.effective_minutes || 0) / 60;
    const focus = Number(dailyData.focus_ratio || 0).toFixed(0);
    ctx.fillStyle = "#ffffff";
    ctx.font = exportFont(900, 46 * S);
    ctx.fillText("今日专注", 52 * S, 150);
    ctx.fillText(`${eff.toFixed(1)} 小时`, 52 * S, 220);
    ctx.fillStyle = "#f8b84e";
    ctx.font = exportFont(700, 20 * S);
    ctx.fillText(`专注率 ${focus}% · 状态自评 ${
        dailyData.ratings && dailyData.ratings.length
            ? (dailyData.ratings.reduce((s, r) => s + Number(r.value || 0), 0) / dailyData.ratings.length).toFixed(1)
            : "-"
    }`, 52 * S, 306);

    // 分隔线
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.lineWidth = 1 * S;
    ctx.beginPath();
    ctx.moveTo(52 * S, 360);
    ctx.lineTo(W - 52 * S, 360);
    ctx.stroke();

    // 时间轴横幅
    const tlX = 52 * S, tlW = W - 104 * S, tlY = 400, tlH = 96 * S;
    drawRoundedRect(ctx, tlX, tlY, tlW, tlH, 10 * S);
    ctx.fillStyle = "rgba(255,255,255,0.08)";
    ctx.fill();
    const segColors = {
        activeOnDuty: "#f8b84e",
        onDutyIdle: "#64748b",
        activeOffDuty: "#38bdf8",
        offDutyIdle: "rgba(255,255,255,0.06)",
    };
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        if (end <= start) continue;
        const x = tlX + (start / 1440) * tlW;
        const w = ((end - start) / 1440) * tlW;
        ctx.fillStyle = segColors[cls] || "rgba(255,255,255,0.06)";
        ctx.fillRect(x, tlY + 6 * S, Math.max(w, 1 * S), tlH - 12 * S);
    }
    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.font = exportFont(500, 10 * S);
    ctx.textAlign = "left";
    ctx.fillText("00", tlX, tlY + tlH + 18 * S);
    ctx.textAlign = "center";
    ctx.fillText("06", tlX + tlW * 0.25, tlY + tlH + 18 * S);
    ctx.fillText("12", tlX + tlW * 0.5, tlY + tlH + 18 * S);
    ctx.fillText("18", tlX + tlW * 0.75, tlY + tlH + 18 * S);
    ctx.textAlign = "right";
    ctx.fillText("24", tlX + tlW, tlY + tlH + 18 * S);
    ctx.textAlign = "left";

    // 打卡
    const marks = dailyData.checkin_marks || [];
    const mY = tlY + tlH + 70 * S;
    const mText = marks[0]
        ? `${marks[0].time} ${marks[0].current ? "进行中（截至当前）" : "开工"}`
        : "--:-- 开工";
    const mText2 = marks[1]
        ? `${marks[1].time} ${marks[1].current ? "进行中（截至当前）" : "收工"}`
        : "--:-- 收工";
    ctx.fillStyle = "#ffffff";
    ctx.font = exportFont(700, 16 * S);
    ctx.fillText(mText, tlX, mY);
    ctx.textAlign = "right";
    ctx.fillText(mText2, tlX + tlW, mY);
    ctx.textAlign = "left";

    // 正文统计
    const bodyY = mY + 90 * S;
    const onDutyH = (Number(dailyData.on_duty_minutes || 0) / 60).toFixed(1);
    const activeH = (Number(dailyData.active_minutes || 0) / 60).toFixed(1);
    const slackMin = Math.max(0, Number(dailyData.on_duty_minutes || 0) - Number(dailyData.effective_minutes || 0));
    const lines = [
        ["在岗", `${onDutyH}h`, "#ffffff"],
        ["活跃", `${activeH}h`, "#38bdf8"],
        ["摸鱼", `${slackMin}m`, "#f8b84e"],
    ];
    lines.forEach(([k, v, c], i) => {
        const y = bodyY + i * 56 * S;
        ctx.fillStyle = "rgba(255,255,255,0.5)";
        ctx.font = exportFont(500, 14 * S);
        ctx.fillText(k, tlX, y);
        ctx.fillStyle = c;
        ctx.font = exportFont(800, 20 * S);
        ctx.fillText(v, tlX + tlW - ctx.measureText(v).width, y);
    });

    ctx.fillStyle = "rgba(255,255,255,0.35)";
    ctx.font = exportFont(500, 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("FLOWTRACE 每日快讯 · 时间有迹可循", W / 2, H - 48 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── J-2 · 电影票根（日图）──────────────────────────────
async function composeDailyTicketBlob(dailyData) {
    const S = 2, W = 480 * S, H = 780 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    ctx.fillStyle = "#141e36";
    ctx.fillRect(0, 0, W, H);
    // 顶部光带
    const strip = ctx.createLinearGradient(0, 0, W, 0);
    strip.addColorStop(0, "#f5c842");
    strip.addColorStop(1, "#e8a020");
    ctx.fillStyle = strip;
    ctx.fillRect(0, 0, W, 8 * S);

    const dateStr = dailyData.date || fmtDate(new Date());
    const weekday = WEEKDAY_CN[new Date(`${dateStr}T00:00:00`).getDay()] || "";
    // 影城抬头
    ctx.textAlign = "center";
    ctx.fillStyle = "#f5c842";
    ctx.font = exportFont(800, 16 * S);
    ctx.fillText("FLOWTRACE CINEMA 影城", W / 2, 48);
    ctx.fillStyle = "rgba(255,255,255,0.5)";
    ctx.font = exportFont(500, 11 * S);
    ctx.fillText(`${weekday} ${dateStr} · 今日专场`, W / 2, 88);

    // 片名
    const eff = Number(dailyData.effective_minutes || 0) / 60;
    ctx.fillStyle = "#ffffff";
    ctx.font = exportFont(900, 44 * S);
    ctx.fillText(`《有效专注 ${eff.toFixed(1)} 小时》`, W / 2, 150);
    ctx.fillStyle = "rgba(255,255,255,0.5)";
    ctx.font = exportFont(500, 13 * S);
    ctx.fillText("主演：你 · 类型：在岗奋斗 · 语言：专注", W / 2, 262);

    // 信息行
    const marks = dailyData.checkin_marks || [];
    const onDutyH = (Number(dailyData.on_duty_minutes || 0) / 60).toFixed(1);
    const ratings = dailyData.ratings || [];
    const avg = ratings.length ? (ratings.reduce((s, r) => s + Number(r.value || 0), 0) / ratings.length).toFixed(1) : "-";
    const info = [
        ["场次", marks[0] ? `${marks[0].time} — ${marks[1] ? (marks[1].current ? "进行中（截至当前）" : marks[1].time) : "未完"}` : "--:--"],
        ["座位", `${avg} ★`],
        ["票价", `${onDutyH}h`],
        ["影厅", "专注厅"],
    ];
    info.forEach(([k, v], i) => {
        const y = 330 + i * 60;
        ctx.fillStyle = "rgba(255,255,255,0.45)";
        ctx.font = exportFont(500, 12 * S);
        ctx.fillText(k, 64, y);
        ctx.fillStyle = "#ffffff";
        ctx.font = exportFont(700, 15 * S);
        ctx.fillText(v, 64 + 110, y);
    });

    // 影片时间轴
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    ctx.font = exportFont(600, 11 * S);
    ctx.textAlign = "center";
    ctx.fillText("—— 放映时间轴 ——", W / 2, 580);
    const tlX = 64, tlW = W - 128, tlY = 622, tlH = 70 * S;
    drawRoundedRect(ctx, tlX, tlY, tlW, tlH, 8 * S);
    ctx.fillStyle = "rgba(255,255,255,0.08)";
    ctx.fill();
    const segColors = {
        activeOnDuty: "#f5c842",
        onDutyIdle: "#8b9bb8",
        activeOffDuty: "#7dd3fc",
        offDutyIdle: "rgba(255,255,255,0.06)",
    };
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        if (end <= start) continue;
        const x = tlX + (start / 1440) * tlW;
        const w = ((end - start) / 1440) * tlW;
        ctx.fillStyle = segColors[cls] || "rgba(255,255,255,0.06)";
        ctx.fillRect(x, tlY + 5 * S, Math.max(w, 1 * S), tlH - 10 * S);
    }
    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.font = exportFont(500, 10 * S);
    ctx.textAlign = "left";
    ctx.fillText("00", tlX, tlY + tlH + 16 * S);
    ctx.textAlign = "center";
    ctx.fillText("12", tlX + tlW * 0.5, tlY + tlH + 16 * S);
    ctx.textAlign = "right";
    ctx.fillText("24", tlX + tlW, tlY + tlH + 16 * S);
    ctx.textAlign = "center";

    // 撕口
    const tearY = 860;
    ctx.strokeStyle = "rgba(255,255,255,0.3)";
    ctx.lineWidth = 2 * S;
    ctx.setLineDash([12 * S, 10 * S]);
    ctx.beginPath();
    ctx.moveTo(40, tearY);
    ctx.lineTo(W - 40, tearY);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#141e36";
    ctx.beginPath();
    ctx.arc(40, tearY, 12 * S, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(W - 40, tearY, 12 * S, 0, Math.PI * 2);
    ctx.fill();

    // 副券统计
    const activeH = (Number(dailyData.active_minutes || 0) / 60).toFixed(1);
    const slackMin = Math.max(0, Number(dailyData.on_duty_minutes || 0) - Number(dailyData.effective_minutes || 0));
    const stubStats = [
        { v: `${avg} ★`, l: "今日自评" },
        { v: `${activeH}h`, l: "活跃时长" },
        { v: `${slackMin}m`, l: "摸鱼" },
    ];
    const cardW = (W - 128 - 2 * 20 * S) / 3;
    stubStats.forEach((s, i) => {
        const cx = 64 + i * (cardW + 20 * S);
        drawRoundedRect(ctx, cx, 930, cardW, 120 * S, 10 * S);
        ctx.fillStyle = "rgba(255,255,255,0.08)";
        ctx.fill();
        ctx.fillStyle = "#f5c842";
        ctx.font = exportFont(800, 20 * S);
        ctx.fillText(s.v, cx + cardW / 2, 950);
        ctx.fillStyle = "rgba(255,255,255,0.5)";
        ctx.font = exportFont(500, 11 * S);
        ctx.fillText(s.l, cx + cardW / 2, 1010);
    });

    // 副券
    ctx.fillStyle = "#f5c842";
    ctx.font = exportFont(800, 18 * S);
    ctx.fillText("副券 · 已记录", W / 2, 1140);
    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.font = exportFont(500, 11 * S);
    ctx.fillText("FLOWTRACE · 时间有迹可循", W / 2, 1190);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── J-3 · 登机牌（日图）────────────────────────────────
async function composeDailyBoardingBlob(dailyData) {
    const S = 2, W = 480 * S, H = 780 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, W, H);
    // 顶部渐变条
    const accent = ctx.createLinearGradient(0, 0, W, 0);
    accent.addColorStop(0, "#0e9f6e");
    accent.addColorStop(0.5, "#38bdf8");
    accent.addColorStop(1, "#6366f1");
    ctx.fillStyle = accent;
    ctx.fillRect(0, 0, W, 8 * S);

    const dateStr = dailyData.date || fmtDate(new Date());
    // 抬头
    ctx.fillStyle = "#111827";
    ctx.font = exportFont(800, 15 * S);
    ctx.fillText("FLOWTRACE AIR", 56, 48);
    ctx.fillStyle = "#6b7280";
    ctx.font = exportFont(600, 11 * S);
    ctx.textAlign = "right";
    ctx.fillText(`航班 FT-${String(dateStr).replace(/-/g, "").slice(4)} · ${dateStr}`, W - 56, 52);
    ctx.textAlign = "left";

    // 出发 → 到达
    const eff = Number(dailyData.effective_minutes || 0) / 60;
    ctx.fillStyle = "#111827";
    ctx.font = exportFont(900, 30 * S);
    ctx.fillText("FLOWTRACE", 56, 130);
    ctx.fillStyle = "#38bdf8";
    ctx.font = exportFont(900, 34 * S);
    ctx.textAlign = "center";
    ctx.fillText("➜", W / 2, 128);
    ctx.textAlign = "left";
    ctx.fillStyle = "#111827";
    ctx.font = exportFont(900, 30 * S);
    ctx.textAlign = "right";
    ctx.fillText("FOCUS", W - 56, 130);
    ctx.textAlign = "left";
    ctx.fillStyle = "#6b7280";
    ctx.font = exportFont(500, 12 * S);
    ctx.fillText("出发 · 开始工作", 56, 186);
    ctx.textAlign = "right";
    ctx.fillText(`到达 · 有效专注 ${eff.toFixed(1)}h`, W - 56, 186);
    ctx.textAlign = "left";

    // 信息网格
    const marks = dailyData.checkin_marks || [];
    const ratings = dailyData.ratings || [];
    const avg = ratings.length ? (ratings.reduce((s, r) => s + Number(r.value || 0), 0) / ratings.length).toFixed(1) : "-";
    const focus = Number(dailyData.focus_ratio || 0).toFixed(0);
    const grid = [
        ["登机", marks[0] ? (marks[0].current ? "进行中（截至当前）" : marks[0].time) : "--:--"],
        ["登机口", marks[1] ? (marks[1].current ? "进行中（截至当前）" : marks[1].time) : "--:--"],
        ["座位", `${avg} ★`],
        ["舱位", `专注 ${focus}%`],
    ];
    const gX = 56, gW = (W - 112 - 20 * S) / 2, gY = 260, gH = 92 * S;
    grid.forEach(([k, v], i) => {
        const cx = gX + (i % 2) * (gW + 20 * S);
        const cy = gY + Math.floor(i / 2) * (gH + 16 * S);
        drawRoundedRect(ctx, cx, cy, gW, gH, 8 * S);
        ctx.fillStyle = "#f3f4f6";
        ctx.fill();
        ctx.fillStyle = "#6b7280";
        ctx.font = exportFont(600, 10 * S);
        ctx.fillText(k, cx + 20 * S, cy + 14 * S);
        ctx.fillStyle = "#111827";
        ctx.font = exportFont(800, 22 * S);
        ctx.fillText(v, cx + 20 * S, cy + 46 * S);
    });

    // 航线时间轴
    const tlX = 56, tlW = W - 112, tlY = 700, tlH = 60 * S;
    drawRoundedRect(ctx, tlX, tlY, tlW, tlH, 30 * S);
    ctx.fillStyle = "#f0fdf9";
    ctx.fill();
    const segColors = {
        activeOnDuty: "#0e9f6e",
        onDutyIdle: "#fbbf24",
        activeOffDuty: "#38bdf8",
        offDutyIdle: "#e5e7eb",
    };
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        if (end <= start) continue;
        const x = tlX + (start / 1440) * tlW;
        const w = ((end - start) / 1440) * tlW;
        ctx.fillStyle = segColors[cls] || "#e5e7eb";
        drawRoundedRect(ctx, x + 2 * S, tlY + 6 * S, Math.max(w - 4 * S, 2 * S), tlH - 12 * S, (tlH - 12 * S) / 2);
        ctx.fill();
    }
    ctx.fillStyle = "#9ca3af";
    ctx.font = exportFont(500, 10 * S);
    ctx.textAlign = "left";
    ctx.fillText("00", tlX, tlY + tlH + 16 * S);
    ctx.textAlign = "center";
    ctx.fillText("12", tlX + tlW * 0.5, tlY + tlH + 16 * S);
    ctx.textAlign = "right";
    ctx.fillText("24", tlX + tlW, tlY + tlH + 16 * S);
    ctx.textAlign = "left";

    // 撕口 + 副券
    const tearY = tlY + tlH + 80 * S;
    ctx.strokeStyle = "#d1d5db";
    ctx.lineWidth = 2 * S;
    ctx.setLineDash([12 * S, 10 * S]);
    ctx.beginPath();
    ctx.moveTo(40, tearY);
    ctx.lineTo(W - 40, tearY);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(40, tearY, 12 * S, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(W - 40, tearY, 12 * S, 0, Math.PI * 2);
    ctx.fill();

    // 伪条码
    const bX = 56, bY = tearY + 44 * S, bW = W - 112, bH = 56 * S;
    ctx.fillStyle = "#111827";
    let bx = bX;
    const pattern = [3, 1, 2, 1, 4, 1, 2, 3, 1, 2, 4, 1, 3, 1, 2, 1, 4, 2, 1, 3];
    pattern.forEach((w, i) => {
        const barW = w * 2 * S;
        if (i % 2 === 0) ctx.fillRect(bx, bY, barW, bH);
        bx += barW;
    });
    ctx.fillStyle = "#9ca3af";
    ctx.font = exportFont(600, 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("FT" + String(dateStr).replace(/-/g, ""), W / 2, bY + bH + 14 * S);
    ctx.textAlign = "left";
    ctx.fillStyle = "#6b7280";
    ctx.font = exportFont(500, 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("FLOWTRACE · 时间有迹可循 · 感谢记录每一天", W / 2, H - 36 * S);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

// ── V-1 · 磁带随身听（日图）────────────────────────────
async function composeDailyWalkmanBlob(dailyData) {
    const S = 2, W = 480 * S, H = 780 * S;
    const { canvas, ctx } = createExportCanvas(W, H);
    const bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#1a0b2e");
    bg.addColorStop(0.5, "#4a1a6b");
    bg.addColorStop(1, "#b967ff");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);

    const dateStr = dailyData.date || fmtDate(new Date());
    const eff = Number(dailyData.effective_minutes || 0) / 60;
    const focus = Number(dailyData.focus_ratio || 0).toFixed(0);
    const onDutyH = (Number(dailyData.on_duty_minutes || 0) / 60).toFixed(1);
    const activeH = (Number(dailyData.active_minutes || 0) / 60).toFixed(1);
    const slackMin = Math.max(0, Number(dailyData.on_duty_minutes || 0) - Number(dailyData.effective_minutes || 0));
    const ratings = dailyData.ratings || [];
    const avg = ratings.length ? (ratings.reduce((s, r) => s + Number(r.value || 0), 0) / ratings.length).toFixed(1) : "-";
    const marks = dailyData.checkin_marks || [];

    // 顶部
    ctx.fillStyle = "rgba(255,255,255,0.6)";
    ctx.font = exportFont(700, 11 * S);
    ctx.fillText("FLOWTRACE ♆ WALKMAN", 80, 64);
    ctx.textAlign = "right";
    ctx.fillText(String(dateStr).slice(5).replace("-", "."), W - 80, 66);
    ctx.textAlign = "left";

    // 随身听机身
    const wmX = 80, wmY = 170, wmW = W - 160, wmH = 430;
    const wmGrad = ctx.createLinearGradient(wmX, wmY, wmX + wmW, wmY + wmH);
    wmGrad.addColorStop(0, "#3a1068");
    wmGrad.addColorStop(1, "#220a40");
    drawRoundedRect(ctx, wmX, wmY, wmW, wmH, 36);
    ctx.fillStyle = wmGrad;
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.fillStyle = "rgba(255,255,255,0.5)";
    ctx.font = exportFont(800, 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("FOCUS TAPE · SIDE A", W / 2, wmY + 26);
    ctx.textAlign = "left";

    // 磁带窗
    const wwX = wmX + 30, wwY = wmY + 60, wwW = wmW - 60, wwH = 150;
    drawRoundedRect(ctx, wwX, wwY, wwW, wwH, 16);
    ctx.fillStyle = "#120726";
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.2)";
    ctx.lineWidth = 2;
    ctx.stroke();
    // 卷轴
    const reelY = wwY + wwH / 2;
    [[wwX + 70, reelY], [wwX + wwW - 70, reelY]].forEach(([rx, ry]) => {
        ctx.beginPath();
        ctx.arc(rx, ry, 40, 0, Math.PI * 2);
        ctx.fillStyle = "#0a0a12";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(rx, ry, 40, 0, Math.PI * 2);
        ctx.strokeStyle = "#2a1248";
        ctx.lineWidth = 14;
        ctx.stroke();
    });
    // 磁带条 = 时间轴
    const tapeY = reelY - 18, tapeH = 36;
    const segColors = {
        activeOnDuty: "#01cdfe",
        onDutyIdle: "#ffd640",
        offDuty: "#2a1248",
        activeOffDuty: "#ff71ce",
    };
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        const start = Number(seg.start_min || 0);
        const end = Number(seg.end_min || 0);
        if (end <= start) continue;
        const x = wwX + 90 + (start / 1440) * (wwW - 180);
        const w = ((end - start) / 1440) * (wwW - 180);
        ctx.fillStyle = segColors[cls] || "#2a1248";
        ctx.fillRect(x, tapeY, Math.max(w, 1), tapeH);
    }
    // 打卡标记
    for (const m of marks) {
        const [hh, mi] = String(m.time || "00:00").split(":").map(Number);
        const x = wwX + 90 + (((hh || 0) * 60 + (mi || 0)) / 1440) * (wwW - 180);
        ctx.fillStyle = "#ff3355";
        ctx.fillRect(x - 1.5, tapeY - 6, 3, tapeH + 12);
    }
    // 控制键
    const btns = ["◀◀", "▶", "■", "▶▶"];
    btns.forEach((b, i) => {
        const bx = W / 2 - 90 + i * 60;
        const by = wwY + wwH + 30;
        drawRoundedRect(ctx, bx, by, 48, 34, 4);
        ctx.fillStyle = i === 1 ? "#ff71ce" : "rgba(255,255,255,0.12)";
        ctx.fill();
        ctx.fillStyle = i === 1 ? "#1a0b2e" : "rgba(255,255,255,0.8)";
        ctx.font = exportFont(900, 10 * S);
        ctx.textAlign = "center";
        ctx.fillText(b, bx + 24, by + 9);
        ctx.textAlign = "left";
    });
    // EQ 均衡器（= 每 3 小时活跃分钟，归一化）
    const eqY = wwY + wwH + 84, eqH = 96;
    const bucketMin = new Array(8).fill(0);
    for (const seg of dailyData.timeline_segments || []) {
        const cls = exportSegmentClass(seg);
        if (cls !== "activeOnDuty" && cls !== "activeOffDuty") continue;
        const s0 = Number(seg.start_min || 0), e0 = Number(seg.end_min || 0);
        for (let b = 0; b < 8; b++) {
            const lo = Math.max(s0, b * 180), hi = Math.min(e0, b * 180 + 180);
            if (hi > lo) bucketMin[b] += hi - lo;
        }
    }
    const eqMax = Math.max(1, ...bucketMin);
    const eqGap = 20, eqW = 60;
    bucketMin.forEach((min, i) => {
        const bx = wmX + 90 + i * (eqW + eqGap);
        const bh = min === 0 ? 6 : Math.max(10, (min / eqMax) * eqH);
        const g = ctx.createLinearGradient(0, eqY + eqH - bh, 0, eqY + eqH);
        g.addColorStop(0, "#01cdfe");
        g.addColorStop(1, "#ff71ce");
        drawRoundedRect(ctx, bx, eqY + eqH - bh, eqW, bh, 3);
        ctx.fillStyle = g;
        ctx.fill();
    });

    // 大数字（霓虹描边）
    ctx.textAlign = "center";
    ctx.font = exportFont(900, 110 * S);
    ctx.lineJoin = "round";
    // 发光层
    ctx.strokeStyle = "rgba(1,205,254,0.35)";
    ctx.lineWidth = 18;
    ctx.strokeText(eff.toFixed(1), W / 2, 700);
    // 错位色层
    ctx.strokeStyle = "rgba(255,113,206,0.55)";
    ctx.lineWidth = 6;
    ctx.strokeText(eff.toFixed(1), W / 2 + 10, 710);
    // 主描边 + 淡填充
    ctx.strokeStyle = "#01cdfe";
    ctx.lineWidth = 6;
    ctx.strokeText(eff.toFixed(1), W / 2, 700);
    ctx.fillStyle = "rgba(255,255,255,0.06)";
    ctx.fillText(eff.toFixed(1), W / 2, 700);
    ctx.lineJoin = "miter";
    ctx.fillStyle = "#ffd640";
    ctx.font = exportFont(800, 15 * S);
    ctx.fillText("HOURS OF FOCUS", W / 2, 930);
    ctx.textAlign = "left";

    // 标签条（在大数字下方，避免与数字底缘/unit 重叠）
    const tagY = 990;
    drawRoundedRect(ctx, 80, tagY, W - 160, 70, 14);
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    ctx.font = exportFont(700, 12 * S);
    const tagTxt = !marks.length
        ? "— 今日未打卡 —"
        : marks.length === 1
            ? `${marks[0].time} PLAY ▸ ${marks[0].current ? "进行中" : "--:-- STOP"}`
            : `${marks[0].time} PLAY ▸ ${marks[marks.length - 1].time} STOP`;
    ctx.fillText(tagTxt, 104, tagY + 22);
    ctx.fillStyle = "#ffd640";
    ctx.textAlign = "right";
    ctx.fillText(`专注率 ${focus}%`, W - 104, tagY + 22);
    ctx.textAlign = "left";

    // 统计三卡
    const statsY = tagY + 110;
    const stats = [
        { v: `${onDutyH}h`, l: "磁带长度" },
        { v: `${avg}★`, l: "音质·自评" },
        { v: `${slackMin}m`, l: "杂音·摸鱼" },
    ];
    const cardW = (W - 160 - 40) / 3;
    stats.forEach((s, i) => {
        const cx = 80 + i * (cardW + 20);
        drawRoundedRect(ctx, cx, statsY, cardW, 116, i === 1 ? 26 : 34);
        ctx.fillStyle = "rgba(0,0,0,0.25)";
        ctx.fill();
        ctx.strokeStyle = "rgba(255,255,255,0.3)";
        ctx.lineWidth = 3;
        ctx.stroke();
        ctx.fillStyle = "#ffd640";
        ctx.font = exportFont(900, 20 * S);
        ctx.textAlign = "center";
        ctx.fillText(s.v, cx + cardW / 2, statsY + 24);
        ctx.fillStyle = "rgba(255,255,255,0.7)";
        ctx.font = exportFont(600, 9 * S);
        ctx.fillText(s.l, cx + cardW / 2, statsY + 66);
        ctx.textAlign = "left";
    });

    // 底部透视网格
    const gh = 1260;
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.lineWidth = 1;
    for (let i = 0; i < 7; i++) {
        const y = gh + i * 44;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
    }
    for (let i = 0; i <= 16; i++) {
        const bx = (i / 16) * W;
        ctx.beginPath();
        ctx.moveTo(W / 2, gh);
        ctx.lineTo(bx, H);
        ctx.stroke();
    }

    // footer
    ctx.fillStyle = "rgba(255,255,255,0.5)";
    ctx.font = exportFont(600, 10 * S);
    ctx.textAlign = "center";
    ctx.fillText("A E S T H E T I C · 时间有迹可循", W / 2, H - 60);
    ctx.textAlign = "left";
    return canvasBlob(canvas);
}

