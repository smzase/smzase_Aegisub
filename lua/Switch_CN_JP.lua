script_name = "多语种字幕工具箱"
script_description = "包含中日切换与多语种同选功能"
script_author = "Assistant"
script_version = "5.0"

---------------------------------------------------------
-- 功能一：切换中日字幕 (Switch_CN_JP)
---------------------------------------------------------
local function find_counterpart(subtitles, line_idx)
    local current_line = subtitles[line_idx]
    if not current_line or current_line.class ~= "dialogue" then return nil end

    local style = current_line.style or ""
    local target_style = nil

    if string.find(style, "CN") then target_style = string.gsub(style, "CN", "JP")
    elseif string.find(style, "JP") then target_style = string.gsub(style, "JP", "CN")
    elseif string.find(style, "中") then target_style = string.gsub(style, "中", "日")
    elseif string.find(style, "日") then target_style = string.gsub(style, "日", "中")
    else return nil end

    local best_match = nil
    local min_dist = math.huge

    for i = 1, #subtitles do
        local l = subtitles[i]
        if l.class == "dialogue" and l.style == target_style then
            if l.start_time == current_line.start_time and l.end_time == current_line.end_time then
                local dist = math.abs(i - line_idx)
                if dist < min_dist then
                    min_dist = dist
                    best_match = i
                end
            end
        end
    end
    
    if best_match then return best_match end

    for i = 1, #subtitles do
        local l = subtitles[i]
        if l.class == "dialogue" and l.style == target_style then
            if l.start_time < current_line.end_time and l.end_time > current_line.start_time then
                return i
            end
        end
    end

    return nil
end

function toggle_cn_jp(subtitles, selected_lines, active_line)
    if #selected_lines == 0 then return selected_lines, active_line end

    local new_selected = {}
    local new_active = active_line
    local changed = false

    for _, sel_idx in ipairs(selected_lines) do
        local counterpart = find_counterpart(subtitles, sel_idx)
        if counterpart then
            table.insert(new_selected, counterpart)
            if sel_idx == active_line then new_active = counterpart end
            changed = true
        else
            table.insert(new_selected, sel_idx)
        end
    end

    if not changed then
        return selected_lines, active_line
    end

    local line = subtitles[new_active]
    local orig_effect = line.effect
    line.effect = orig_effect .. " "
    subtitles[new_active] = line
    aegisub.set_undo_point("切换语言 (刷新)")
    
    line.effect = orig_effect
    subtitles[new_active] = line
    aegisub.set_undo_point("切换语言")

    return new_selected, new_active
end


---------------------------------------------------------
-- 功能二：选中所有同时间轴语言 (Select_All_Languages)
---------------------------------------------------------
function select_all_languages(subtitles, selected_lines, active_line)
    if #selected_lines == 0 then return selected_lines, active_line end

    local new_selected = {}
    local target_times = {}

    for _, sel_idx in ipairs(selected_lines) do
        local line = subtitles[sel_idx]
        if line.class == "dialogue" then
            local time_key = tostring(line.start_time) .. "_" .. tostring(line.end_time)
            target_times[time_key] = true
        end
    end

    for i = 1, #subtitles do
        local line = subtitles[i]
        if line.class == "dialogue" then
            local time_key = tostring(line.start_time) .. "_" .. tostring(line.end_time)
            if target_times[time_key] then
                table.insert(new_selected, i)
            end
        end
    end

    return new_selected, active_line
end

-- 【关键修改】：使用 "主菜单名/子功能名" 的格式来实现二级菜单折叠
aegisub.register_macro("：批选行/Switch_CN_JP", "一键切换中日字幕 (画面跟随跳转)", toggle_cn_jp)
aegisub.register_macro("：批选行/Select_All_Languages", "一键选中所有同时间轴的多语种行 (留在原地)", select_all_languages)

-- automation/lua/Switch_CN_JP/：批选行/Select_All_Languages
-- automation/lua/Switch_CN_JP/：批选行/Switch_CN_JP