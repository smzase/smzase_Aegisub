-- Aegisub 自动化脚本: 添加 {\blur} 标签（直接前置）
script_name = "添加 blur 标签（直接前置）"
script_description = "在字幕行最前面插入 blur，无视现有标签"
script_author = "ChatGPT"
script_version = "1.2"

local previous_blur = "3.0"         -- 默认 blur 数值
local previous_mode = "selected"    -- 默认操作模式："selected" 或 "all"

function add_blur(subs, sel)
    -- 显示对话框
    local dialog_config = {
        {class="label", label="请输入 blur 数值：", x=0, y=0},
        {name="blur_value", class="edit", value=previous_blur, x=1, y=0},
        
        {class="label", label="操作范围：", x=0, y=1},
        {name="mode", class="dropdown", items={"所选行", "全部行"}, value=(previous_mode == "all") and "全部行" or "所选行", x=1, y=1},
    }

    local pressed, result = aegisub.dialog.display(dialog_config, {"确定", "取消"})
    if pressed ~= "确定" then return end

    local blur_val = result.blur_value
    local mode = (result.mode == "全部行") and "all" or "selected"

    -- 记住设置
    previous_blur = blur_val
    previous_mode = mode

    -- 执行插入
    local target_lines = (mode == "all") and range(1, #subs) or sel

    for i = 1, #target_lines do
        local idx = target_lines[i]
        local line = subs[idx]
        if line.class == "dialogue" then
            -- 直接在最前面添加 {\blurX}，不管是否有其他标签
            line.text = "{\\blur" .. blur_val .. "}" .. line.text
            subs[idx] = line
        end
    end
end

function range(start_idx, end_idx)
    local r = {}
    for i = start_idx, end_idx do
        r[#r+1] = i
    end
    return r
end

aegisub.register_macro(script_name, script_description, add_blur)