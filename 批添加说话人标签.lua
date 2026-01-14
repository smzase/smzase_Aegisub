-- 脚本信息
script_name = "批量添加说话人标签"
script_description = "对选中行弹出窗口，为不同的说话人批量添加ASS标签（如颜色代码）"
script_author = "gemini 3 pro"
script_version = "1.0"

function add_speaker_tags(subtitles, selected_lines, active_line)
    -- 1. 扫描选中行，获取所有不重复的说话人 (Actor)
    local actors = {}
    local actor_list = {}
    
    for _, i in ipairs(selected_lines) do
        local line = subtitles[i]
        local actor = line.actor
        
        -- 只有当说话人不为空，且尚未记录时才添加
        if actor ~= "" and not actors[actor] then
            actors[actor] = true
            table.insert(actor_list, actor)
        end
    end
    
    -- 如果没有找到任何说话人，提示用户
    if #actor_list == 0 then
        aegisub.debug.out("在选中的行中未找到设置了'说话人(Actor)'的行。")
        return
    end
    
    -- 2. 构建配置对话框 UI
    local dialog_config = {}
    
    -- 添加一个说明标签
    table.insert(dialog_config, {
        class = "label",
        x = 0, y = 0, width = 2,
        label = "请输入对应说话人的标签 (例如: \\c&H0000FF&):"
    })
    
    -- 循环生成说话人列表和输入框
    for i, actor in ipairs(actor_list) do
        -- 说话人名字 (Label)
        table.insert(dialog_config, {
            class = "label",
            x = 0, y = i, width = 1,
            label = actor .. ":"
        })
        
        -- 输入框 (Edit), name设为actor名字以便后续读取
        table.insert(dialog_config, {
            class = "edit",
            name = actor,
            x = 1, y = i, width = 20,
            text = "" -- 默认为空，你可以在这里预设值
        })
    end
    
    -- 3. 显示对话框
    local buttons = {"应用", "取消"}
    local pressed, results = aegisub.dialog.display(dialog_config, buttons)
    
    if pressed == "取消" then
        return
    end
    
    -- 4. 处理并应用标签
    for _, i in ipairs(selected_lines) do
        local line = subtitles[i]
        local actor = line.actor
        
        -- 检查该行说话人是否有对应的输入结果
        if results[actor] and results[actor] ~= "" then
            local tag_input = results[actor]
            
            -- 去除首尾空格
            tag_input = tag_input:match("^%s*(.-)%s*$")
            
            -- 自动识别是否带 {}，如果不带则补全
            local final_tag = ""
            if tag_input:match("^{.*}$") then
                final_tag = tag_input
            else
                final_tag = "{" .. tag_input .. "}"
            end
            
            -- 将标签直接放到最前面
            line.text = final_tag .. line.text
            subtitles[i] = line
        end
    end
    
    -- 设置撤销点
    aegisub.set_undo_point("批量添加说话人标签")
end

-- 注册宏

aegisub.register_macro(script_name, script_description, add_speaker_tags)
