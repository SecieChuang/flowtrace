; Flowtrace - AHK v2 客户端主脚本
; 功能: 全局快捷键打卡 + 空闲检测状态机 + 评分弹窗
; 常驻后台运行，通过调用 Python sync.py 与服务端通信

#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; ── 配置加载 ──────────────────────────────────────────
scriptDir := A_ScriptDir
clientDir := scriptDir "\.."
srcDir := clientDir "\src"
configPath := clientDir "\config\config.json"
cfg := LoadConfig(configPath)

IDLE_THRESHOLD := cfg.Has("idle_threshold_seconds") ? cfg["idle_threshold_seconds"] * 1000 : 300000
START_THRESHOLD := cfg.Has("start_threshold_seconds") ? cfg["start_threshold_seconds"] * 1000 : 60000
MONITOR_INTERVAL := cfg.Has("monitor_interval_seconds") ? cfg["monitor_interval_seconds"] * 1000 : 10000
SYNC_INTERVAL := cfg.Has("sync_interval_seconds") ? cfg["sync_interval_seconds"] * 1000 : 60000
RATING_TRIGGER := cfg.Has("rating_trigger_minutes") ? cfg["rating_trigger_minutes"] * 60000 : 5400000
RATING_TIMEOUT := cfg.Has("rating_timeout_seconds") ? cfg["rating_timeout_seconds"] * 1000 : 10000
TOAST_DURATION := cfg.Has("toast_duration_ms") ? cfg["toast_duration_ms"] : 2000
TOAST_OFFSET_RIGHT := cfg.Has("toast_offset_right") ? cfg["toast_offset_right"] : 36
TOAST_OFFSET_BOTTOM := cfg.Has("toast_offset_bottom") ? cfg["toast_offset_bottom"] : 100
SCREENSHOT_INTERVAL := cfg.Has("screenshot_interval_minutes") ? cfg["screenshot_interval_minutes"] * 60000 : 300000
STATE_DIR := clientDir "\runtime"
STATE_PATH := STATE_DIR "\state.json"

; ── 状态变量 ──────────────────────────────────────────
; 状态机: "idle" -> "warmup" -> "active"
currentState := "idle"
warmupStartTick := 0        ; 预热开始时的 A_TickCount
segmentStartDate := ""      ; current segment date
segmentStartTime := ""      ; 当前活跃段的开始时间 HH:MM:SS
segmentStartTick := 0       ; 当前活跃段开始的 tick
lastSyncTick := 0           ; 上次同步的 tick
lastRatingTick := 0         ; 上次弹出评分的 tick（0=未弹过）
isOnDuty := false           ; 是否在岗（打卡状态）
ratingGui := ""             ; 评分窗口引用
toastGui := ""              ; 醒目提示窗引用

; ── 托盘图标 ──────────────────────────────────────────
iconPath := clientDir "\icon\clock-in.ico"
if FileExist(iconPath)
    TraySetIcon(iconPath)

; ── 托盘菜单 ──────────────────────────────────────────
A_IconTip := "Flowtrace - 运行中"
tray := A_TrayMenu
tray.Delete()
tray.Add("状态: 空闲", (*) => "")
tray.Add()
tray.Add("手动刷新队列", (*) => RunSync("flush"))
tray.Add("退出", (*) => ExitApp())

; ── 快捷键 ────────────────────────────────────────────
^+i:: {
    HandleCheckin("clock_in")
}

^+o:: {
    HandleCheckin("clock_out")
}

#HotIf (ratingGui != "")
Esc:: {
    CloseRatingGui()
}
#HotIf

; ── 启动监控定时器 ────────────────────────────────────
SetTimer(MonitorLoop, MONITOR_INTERVAL)
if (!cfg.Has("screenshot_enabled") || cfg["screenshot_enabled"] != 0)
    SetTimer(ScreenshotLoop, SCREENSHOT_INTERVAL)

LoadLocalState()
UpdateTrayStatus(currentState = "active" ? "活跃" : currentState = "warmup" ? "预热中..." : "空闲")

; ── 监控主循环 ────────────────────────────────────────
MonitorLoop() {
    global currentState, warmupStartTick, segmentStartDate, segmentStartTime, segmentStartTick
    global lastSyncTick, lastRatingTick, IDLE_THRESHOLD, START_THRESHOLD
    global SYNC_INTERVAL, RATING_TRIGGER

    idleMs := A_TimeIdlePhysical
    now := A_TickCount

    if (currentState = "idle") {
        ; 空闲状态：检测到活跃则进入预热
        if (idleMs < IDLE_THRESHOLD) {
            currentState := "warmup"
            warmupStartTick := now
            UpdateTrayStatus("预热中...")
        }
    }
    else if (currentState = "warmup") {
        if (idleMs >= START_THRESHOLD) {
            ; 预热期间空闲超过阈值，重置
            currentState := "idle"
            warmupStartTick := 0
            UpdateTrayStatus("空闲")
        }
        else if (now - warmupStartTick >= START_THRESHOLD) {
            ; 预热时间达标，确认活跃
            currentState := "active"
            segmentStartDate := FormatTime(, "yyyy-MM-dd")
            segmentStartTime := FormatTime(, "HH:mm:ss")
            segmentStartTick := now
            lastSyncTick := now
            lastRatingTick := now  ; 段落开始，从现在起计时
            UpdateTrayStatus("活跃")
            ; 同步段落开始
            RunSync("activity start " segmentStartDate " " segmentStartTime)
        }
    }
    else if (currentState = "active") {
        if (idleMs >= IDLE_THRESHOLD) {
            ; 空闲超过阈值，结束段落
            endTime := FormatTime(, "HH:mm:ss")
            RunSync("activity end " segmentStartDate " " segmentStartTime " " endTime)
            currentState := "idle"
            segmentStartDate := ""
            segmentStartTime := ""
            segmentStartTick := 0
            lastRatingTick := 0
            UpdateTrayStatus("空闲")
        }
        else {
            ; 仍然活跃
            ; 定期同步 end_time
            if (now - lastSyncTick >= SYNC_INTERVAL) {
                endTime := FormatTime(, "HH:mm:ss")
                RunSync("activity update " segmentStartDate " " segmentStartTime " " endTime)
                lastSyncTick := now
            }
            ; 检查是否需要弹评分（周期性触发）
            if (lastRatingTick > 0 && (now - lastRatingTick >= RATING_TRIGGER)) {
                lastRatingTick := now  ; 更新为当前时间，下次再等一个周期
                ShowRating()
            }
        }
    }
}

; ── 截屏循环 ──────────────────────────────────────────
; 在岗：总是截图 | 不在岗：仅活跃时截图
ScreenshotLoop() {
    global currentState, isOnDuty
    if (isOnDuty || currentState = "active")
        RunSync("screenshot")
}

; ── 评分弹窗 ──────────────────────────────────────────
ShowRating() {
    global ratingGui, RATING_TIMEOUT

    ; 如果已有弹窗则不重复
    if (ratingGui != "")
        return

    style := Map(
        "bg", "151A23",
        "title", "E8EEF8",
        "sub", "98A6BC",
        "hint", "BBC8DA",
        "score_colors", ["D39AA2", "D8AB88", "9BC3A3", "9CB9DE", "C0ADD8"]
    )

    ratingGui := Gui("+AlwaysOnTop -Caption +ToolWindow")
    ratingGui.BackColor := style["bg"]
    ratingGui.MarginX := 0
    ratingGui.MarginY := 0

    panelW := 810
    panelH := 240
    title := "回顾过去半小时，状态如何？"
    subline := "点击选项提交"

    ratingGui.SetFont("s12 c" style["title"] " Bold", "Segoe UI")
    ratingGui.Add("Text", "x16 y12 w528 Center", title)
    ratingGui.SetFont("s9 c" style["sub"] "", "Segoe UI")
    ratingGui.Add("Text", "x16 y36 w528 Center", subline)

    labels := ["好累/摸鱼", "勉强", "正常", "专注", "火力全开"]
    scoreColors := style["score_colors"]
    chipY := 68
    chipW := 96
    chipH := 44
    chipGap := 8
    chipStartX := 16
    loop 5 {
        x := chipStartX + (A_Index - 1) * (chipW + chipGap)
        ratingGui.SetFont("s10 c" scoreColors[A_Index] " Bold", "Segoe UI")
        btn := ratingGui.Add("Text", "x" x " y" chipY " w" chipW " h" chipH " +Border +Center +0x300", labels[A_Index])
        btn.OnEvent("Click", RatingClick)
    }

    ratingGui.SetFont("s9 c" style["hint"] "", "Segoe UI")
    hintText := "点击即提交评分，Esc 可关闭"
    ratingGui.Add("Text", "x16 y128 w528 Center", hintText)

    ; 固定面板尺寸居中显示
    sw := A_ScreenWidth
    sh := A_ScreenHeight
    posX := (sw - panelW) // 2
    posY := (sh - panelH) // 2
    ratingGui.Show("x" posX " y" posY " w" panelW " h" panelH " NoActivate")
    ApplyRoundedCorner(ratingGui.Hwnd, panelW, panelH, 14)

    ; 超时自动关闭
    SetTimer(RatingTimeout, -RATING_TIMEOUT)
}

RatingClick(ctrl, *) {
    value := ScoreFromLabel(ctrl.Text)
    RunSync("rating " value)
    CloseRatingGui()
    ShowToast("评分 " value " 已记录", "info")
}

ScoreFromLabel(label) {
    if (label = "好累/摸鱼")
        return 1
    if (label = "勉强")
        return 2
    if (label = "正常")
        return 3
    if (label = "专注")
        return 4
    return 5
}

RatingTimeout() {
    CloseRatingGui()
}

CloseRatingGui() {
    global ratingGui
    if (ratingGui != "") {
        try ratingGui.Destroy()
        ratingGui := ""
    }
}

PersistLocalState() {
    global isOnDuty, STATE_DIR, STATE_PATH
    try DirCreate(STATE_DIR)
    content := '{"is_on_duty":' (isOnDuty ? "true" : "false") ',"updated_at":"' A_Now '"}'
    try FileDelete(STATE_PATH)
    FileAppend(content, STATE_PATH, "UTF-8-RAW")
}

LoadLocalState() {
    global isOnDuty, STATE_PATH
    if !FileExist(STATE_PATH)
        return
    try content := FileRead(STATE_PATH, "UTF-8")
    if IsSet(content) {
        if RegExMatch(content, '"is_on_duty"\s*:\s*true')
            isOnDuty := true
        else if RegExMatch(content, '"is_on_duty"\s*:\s*false')
            isOnDuty := false
    }
}

; ── 工具函数 ──────────────────────────────────────────
RunSync(args) {
    global srcDir
    cmd := 'pythonw "' srcDir '\sync.py" ' args
    try {
        Run(cmd, srcDir, "Hide")
    } catch as e {
        ; pythonw 不可用时回退到 python
        try {
            cmd := 'python "' srcDir '\sync.py" ' args
            Run(cmd, srcDir, "Hide")
        }
    }
}

HandleCheckin(action) {
    global isOnDuty, currentState
    if (action = "clock_in")
        isOnDuty := true
    else if (action = "clock_out")
        isOnDuty := false
    PersistLocalState()
    UpdateTrayStatus(currentState = "active" ? "活跃" : currentState = "warmup" ? "预热中..." : "空闲")
    label := action = "clock_in" ? "上班" : "下班"
    ShowToast(label "打卡请求已发送...", "info")
    result := RunSyncCheckin(action)

    level := result.Has("level") ? result["level"] : "warn"
    msg := result.Has("message") ? result["message"] : "打卡失败"
    ShowToast(msg, level)
}

RunSyncCheckin(action) {
    global scriptDir, clientDir, srcDir
    scriptPath := srcDir "\sync.py"
    resultDir := clientDir "\backup"
    resultPath := resultDir "\checkin_result_" A_Now "_" A_TickCount ".txt"
    try DirCreate(resultDir)

    cmdList := [
        'pythonw "' scriptPath '" checkin ' action ' --result-file "' resultPath '"',
        'py -3 "' scriptPath '" checkin ' action ' --result-file "' resultPath '"',
        'python "' scriptPath '" checkin ' action ' --result-file "' resultPath '"',
        'python3 "' scriptPath '" checkin ' action ' --result-file "' resultPath '"'
    ]

    for cmd in cmdList {
        try exitCode := RunWait(cmd, srcDir, "Hide")
        catch
            continue

        if FileExist(resultPath) {
            content := ""
            try content := Trim(FileRead(resultPath, "UTF-8"))
            try FileDelete(resultPath)
            if (content != "")
                return ParseCheckinResult(content)
        }

        if (exitCode = 0)
            return Map("status", "stored", "level", "ok", "message", "打卡已提交")
    }

    return Map("status", "failed", "level", "warn", "message", "打卡失败：无法获取同步结果")
}

ParseCheckinResult(output) {
    lines := StrSplit(output, "`n", "`r")
    for line in lines {
        line := Trim(line)
        if (SubStr(line, 1, 14) = "CHECKIN_RESULT") {
            return ParseCheckinKvLine(line)
        }
    }

    if InStr(output, "已入离线队列") || InStr(output, "已排队")
        return Map("status", "queued", "level", "warn", "message", "服务端不可达，已入离线队列")
    if InStr(output, "成功")
        return Map("status", "stored", "level", "ok", "message", "打卡已落库")

    return Map("status", "failed", "level", "warn", "message", "打卡失败，请检查服务连接")
}

ParseCheckinKvLine(line) {
    kv := Map()
    parts := StrSplit(line, "|")
    for p in parts {
        if (p = "CHECKIN_RESULT")
            continue
        pos := InStr(p, "=")
        if (pos <= 1)
            continue
        key := SubStr(p, 1, pos - 1)
        val := SubStr(p, pos + 1)
        kv[key] := val
    }

    status := kv.Has("status") ? kv["status"] : "failed"
    message := kv.Has("message") ? kv["message"] : ""
    if (message = "") {
        if (status = "stored")
            message := "打卡已落库"
        else if (status = "deduped")
            message := "60秒内重复打卡，已忽略"
        else if (status = "queued")
            message := "服务端不可达，已入离线队列"
        else
            message := "打卡失败"
    }

    level := "info"
    if (status = "stored")
        level := "ok"
    else if (status = "deduped")
        level := "info"
    else if (status = "queued")
        level := "warn"
    else
        level := "warn"

    return Map("status", status, "level", level, "message", message)
}

ShowToast(message, level := "info", durationMs := 0) {
    global TOAST_DURATION, TOAST_OFFSET_RIGHT, TOAST_OFFSET_BOTTOM, toastGui
    if (durationMs <= 0)
        durationMs := TOAST_DURATION

    emoji := "ℹ️"
    title := "状态更新"
    bgColor := "4F46E5" ; info 默认: 高饱和蓝紫
    if (level = "ok")
        emoji := "✅"
    else if (level = "warn")
        emoji := "⚠️"
    else if (level = "info")
        emoji := "📌"

    if (level = "ok") {
        title := "打卡成功"
        bgColor := "0F766E" ; 成功: 强对比青绿
    } else if (level = "warn") {
        title := "需要关注"
        bgColor := "B45309" ; 警告: 高饱和橙棕
    }

    if InStr(message, "请求已发送")
        title := "打卡请求已发送"
    else if InStr(message, "重复打卡")
        title := "重复已自动合并"
    else if InStr(message, "离线队列")
        title := "已切换离线队列"
    else if InStr(message, "失败")
        title := "打卡失败"

    if (toastGui != "") {
        try toastGui.Destroy()
        toastGui := ""
    }

    try {
        toastGui := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x20")
        toastGui.BackColor := bgColor
        toastGui.MarginX := 0
        toastGui.MarginY := 0

        panelW := 360
        panelH := 100

        ; emoji 单独绘制，但与标题保持同字号
        toastGui.SetFont("s16 cFFFFFF Bold", "Segoe UI Emoji")
        toastGui.Add("Text", "x18 y14 w30 h30 Center BackgroundTrans", emoji)

        toastGui.SetFont("s16 cFFFFFF Bold", "Segoe UI")
        toastGui.Add("Text", "x56 y14 w300 h30 Left BackgroundTrans", title)

        toastGui.SetFont("s14 cFFFFFF Bold", "Segoe UI")
        toastGui.Add("Text", "x18 y56 w340 h56 Left BackgroundTrans", message)

        posX := Max(16, A_ScreenWidth - panelW - TOAST_OFFSET_RIGHT)
        posY := Max(16, A_ScreenHeight - panelH - TOAST_OFFSET_BOTTOM)
        toastGui.Show("NoActivate x" posX " y" posY " w" panelW " h" panelH)
        twx := 0
        twy := 0
        realTW := panelW
        realTH := panelH
        WinGetPos(&twx, &twy, &realTW, &realTH, "ahk_id " toastGui.Hwnd)
        ApplyRoundedCorner(toastGui.Hwnd, realTW, realTH, 14)
    } catch {
        ; GUI 失败再回退系统提示
        TrayTip(emoji " " message, title, 1)
        return
    }

    SetTimer(HideToast, -durationMs)
}

HideToast() {
    global toastGui
    if (toastGui != "") {
        try toastGui.Destroy()
        toastGui := ""
    }
}

ApplyRoundedCorner(hwnd, w, h, radius := 20) {
    try {
        hRgn := DllCall("CreateRoundRectRgn"
            , "int", 0
            , "int", 0
            , "int", w
            , "int", h
            , "int", radius
            , "int", radius
            , "ptr")
        if (hRgn)
            DllCall("SetWindowRgn", "ptr", hwnd, "ptr", hRgn, "int", true)
    } catch {
        ; ignore
    }
}

UpdateTrayStatus(status) {
    global isOnDuty
    dutyLabel := isOnDuty ? "在岗" : "离岗"
    fullStatus := dutyLabel " · " status
    A_IconTip := "Flowtrace - " fullStatus
    try {
        tray := A_TrayMenu
        tray.Rename("1&", "状态: " fullStatus)
    }
}

LoadConfig(path) {
    cfg := Map()
    if !FileExist(path)
        return cfg
    try {
        content := FileRead(path, "UTF-8")
        ; 简易 JSON 解析：提取 key-value 对
        pos := 1
        while (pos := RegExMatch(content, '"(\w+)"\s*:\s*("([^"]*)"|([\d.]+)|(true|false))', &m, pos)) {
            key := m[1]
            if (m[3] != "")
                cfg[key] := m[3]
            else if (m[4] != "")
                cfg[key] := Number(m[4])
            else if (m[5] != "")
                cfg[key] := (m[5] = "true" ? 1 : 0)
            pos += m.Len
        }
    }
    return cfg
}
