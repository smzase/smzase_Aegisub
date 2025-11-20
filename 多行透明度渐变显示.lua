-- Aegisub 脚本：文本渐显透明度
script_name = "文本透明渐显"
script_description = "为选中的行依次添加从透明到不透明的 alpha"
script_author = "ChatGPT"
script_version = "1.0"

include("karaskel.lua")

function alpha_fade(subs, sel)
    local total = #sel
    if total == 0 then
        aegisub.debug.out("请选择至少一行！\n")
        return
    end

    for i, line_i in ipairs(sel) do
        local line = subs[line_i]
        local step = math.floor(255 * (1 - (i - 1) / (total - 1))) -- 从255到0
        if total == 1 then step = 0 end -- 仅一行时应为完全不透明
        local hex_alpha = string.format("&H%02X&", step)

        -- 判断已有\alpha标签
        local has_alpha = line.text:match("\\alpha&H%x+&")

        if has_alpha then
            -- 替换原有alpha
            line.text = line.text:gsub("\\alpha&H%x+&", "\\alpha" .. hex_alpha)
        else
            -- 没有alpha，则在已有的特效标签后加入
            if line.text:match("{.-}") then
                line.text = line.text:gsub("{(.-)}", "{%1\\alpha" .. hex_alpha .. "}")
            else
                line.text = "{\\alpha" .. hex_alpha .. "}" .. line.text
            end
        end

        subs[line_i] = line
    end
end

aegisub.register_macro("文本透明渐显", "为选中的行添加逐步显现透明度", alpha_fade)
