-- normalize_punctuation.lua
-- Aegisub Lua Script
-- 作者: ChatGPT (smzase 定制, 优化版)
-- 功能: 智能规范化感叹号/问号，修复多字节匹配Bug，句中全角化，句末半角化。

script_name = "智能感叹号问号规范化"
script_description = "智能转换符号（句中全角去空格，句尾半角化），修复Lua中文字符集匹配Bug。"
script_author = "ChatGPT"
script_version = "1.4"

function normalize_punctuation(line)
    local text = line.text

    ------------------------------------------------------------
    -- 1️⃣ 修正省略号、波浪号后多余空格（修复了原版 […~] 导致的乱码Bug）
    ------------------------------------------------------------
    text = text:gsub("…%s%s+", "… ")
    text = text:gsub("~%s%s+", "~ ")

    ------------------------------------------------------------
    -- 2️⃣ 清理组合符号中间的空格（例如把 "! ?" 缝合为 "!?"）
    ------------------------------------------------------------
    text = text:gsub("！%s+？", "！？")
    text = text:gsub("？%s+！", "？！")
    text = text:gsub("!%s+%?", "!?")
    text = text:gsub("%?%s+!", "?!")

    ------------------------------------------------------------
    -- 3️⃣ 句中符号规范：符号 + 空格 + 其他字符 -> 转为全角并去除空格
    -- 使用 (.) 捕获空格后的第一个字符，确保它不在句末
    ------------------------------------------------------------
    text = text:gsub("！%s+(.)", "！%1")
    text = text:gsub("？%s+(.)", "？%1")
    text = text:gsub("!%s+(.)", "！%1")  -- 半角转全角
    text = text:gsub("%?%s+(.)", "？%1") -- 半角转全角

    ------------------------------------------------------------
    -- 4️⃣ 规范化中间的组合符号 为 "!？" (左半角，右全角)
    ------------------------------------------------------------
    text = text:gsub("！？", "!？")
    text = text:gsub("？！", "!？")
    text = text:gsub("%?!", "!?") -- 顺手纠正英文顺序

    ------------------------------------------------------------
    -- 5️⃣ 句末符号规范：强制转换为半角（包含组合符号），并清理行尾空格
    ------------------------------------------------------------
    -- 句末组合符号
    text = text:gsub("!？%s*$", "!?")
    text = text:gsub("！？%s*$", "!?")
    text = text:gsub("？！%s*$", "!?")
    text = text:gsub("!%?%s*$", "!?")
    text = text:gsub("%?!%s*$", "!?")

    -- 句末单个符号
    text = text:gsub("！%s*$", "!")
    text = text:gsub("？%s*$", "?")

    line.text = text
    return line
end

function process_lines(subs, sel)
    for _, i in ipairs(sel) do
        local line = subs[i]
        if line.class == "dialogue" then
            line = normalize_punctuation(line)
            subs[i] = line
        end
    end
    aegisub.set_undo_point(script_name)
end

aegisub.register_macro(script_name, script_description, process_lines)