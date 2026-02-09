-- change_actor_singlecolumn.lua
-- 批量修改说话人（单列显示，带还原、忽略、初始名）

script_name = "批量修改说话人"
script_description = "批量修改选中行的说话人"
script_author = "ChatGPT5 + Claude-Sonnet-4 + Deepseek v3"
script_version = "2"

-- 会话内全局变量
local modified_actors = {}     -- 已修改/忽略
local initial_actors = {}      -- key=当前名, value=初始名
local restore_override = true  -- 底部勾选："还原按钮不受忽略影响"

-- UTF8长度
local function utf8len(s)
    if not s then return 0 end
    local _, count = s:gsub('[^\128-\193]', '')
    return count
end

local function trim(s) return s and s:gsub("^%s*(.-)%s*$","%1") or "" end

-- 显示文本： 初始 → [当前]
local function display_name(act)
    if initial_actors[act] and initial_actors[act] ~= act then
        return string.format("%s → [%s]", initial_actors[act], act)
    else
        return act
    end
end

-- 显示确认对话框
local function show_confirmation_dialog(replace_map)
    local changes = {}
    local has_changes = false
    
    -- 收集所有变更
    for old_name, new_name in pairs(replace_map) do
        if old_name ~= new_name then
            if new_name == "" then
                table.insert(changes, string.format("%s → [清空]", old_name))
            else
                table.insert(changes, string.format("%s → %s", old_name, new_name))
            end
            has_changes = true
        end
    end
    
    if not has_changes then
        return true -- 无变更，直接确认
    end
    
    -- 创建确认对话框
    local dlg = {
        {class="label", label="即将进行以下修改:", x=0, y=0, width=2, height=1}
    }
    
    table.sort(changes)
    for i, change_text in ipairs(changes) do
        table.insert(dlg, {
            class="label", 
            label=change_text, 
            x=0, y=i, 
            width=2, height=1
        })
    end
    
    local pressed, _ = aegisub.dialog.display(
        dlg,
        {"确认修改", "取消"},
        {ok="确认修改", cancel="取消"}
    )
    
    return pressed == "确认修改"
end

function change_actor_singlecolumn(subs, sel)
    -- 收集选中行的所有说话人（不论是否修改过）
    local actors_map = {}
    for _, i in ipairs(sel) do
        local line = subs[i]
        if line.class == "dialogue" and not line.comment then
            local a = line.actor or ""
            if a ~= "" then
                actors_map[a] = true
            end
        end
    end

    local actor_list = {}
    for k,_ in pairs(actors_map) do table.insert(actor_list, k) end
    table.sort(actor_list)

    if #actor_list == 0 then
        aegisub.debug.out("没有可修改的说话人。\n")
        return
    end

    -- 初始化用户输入的临时存储
    local temp_inputs = {}
    for idx, act in ipairs(actor_list) do
        temp_inputs["chk"..idx] = modified_actors[act] and true or false
        temp_inputs["reset"..idx] = false
        temp_inputs["clear"..idx] = false  -- 添加清空选项的临时存储
        temp_inputs["edit"..idx] = act
    end
    temp_inputs["override_restore"] = restore_override

    -- 主对话框处理逻辑
    local function process_main_dialog()
        local dlg = {}
        table.insert(dlg,{class="label", label="初始 → [当前] | 忽略 | 还原 | 清空 | 新名字", x=0,y=0,width=5,height=1})

        local min_w,max_w = 20,60
        for idx, act in ipairs(actor_list) do
            local row = idx

            -- 左边显示初始 → 当前
            local label_text = display_name(act)
            table.insert(dlg,{class="label", label=label_text, x=0, y=row, width=1, height=1})

            -- 忽略复选框 - 使用临时存储的值
            table.insert(dlg,{class="checkbox", name="chk"..idx, label="忽略", value=temp_inputs["chk"..idx], x=1, y=row, width=1, height=1})

            -- 还原复选框 - 使用临时存储的值
            table.insert(dlg,{class="checkbox", name="reset"..idx, label="还原", value=temp_inputs["reset"..idx], x=2, y=row, width=1, height=1})
            
            -- 清空复选框 - 新添加的功能
            table.insert(dlg,{class="checkbox", name="clear"..idx, label="清空", value=temp_inputs["clear"..idx], x=3, y=row, width=1, height=1})

            -- 输入框显示当前名 - 使用临时存储的值
            local show_text = temp_inputs["edit"..idx]
            local w = math.max(min_w, math.min(max_w, utf8len(show_text)+8))
            table.insert(dlg,{class="edit", name="edit"..idx, text=show_text, x=4, y=row, width=w, height=1})
        end

        -- 底部全局勾选 - 使用临时存储的值
        table.insert(dlg,{class="checkbox", name="override_restore", label="还原按钮不受忽略影响", value=temp_inputs["override_restore"], x=0, y=#actor_list+2, width=5, height=1})

        local pressed,res = aegisub.dialog.display(dlg,{"确定","取消"},{ok="确定", cancel="取消"})
        return pressed, res
    end

    -- 处理主对话框直到用户确认或取消
    while true do
        local pressed, res = process_main_dialog()
        if pressed ~= "确定" then return end
        
        -- 保存用户输入到临时存储中
        for idx, _ in ipairs(actor_list) do
            temp_inputs["chk"..idx] = res["chk"..idx]
            temp_inputs["reset"..idx] = res["reset"..idx]
            temp_inputs["clear"..idx] = res["clear"..idx]
            temp_inputs["edit"..idx] = res["edit"..idx]
        end
        temp_inputs["override_restore"] = res.override_restore
        
        restore_override = res.override_restore or true

        local replace_map = {}
        for idx, act in ipairs(actor_list) do
            local ignore = res["chk"..idx]
            local reset = res["reset"..idx]
            local clear = res["clear"..idx]

            if clear then
                -- 清空操作优先级最高
                replace_map[act] = ""
            elseif reset then
                -- 还原一次性操作，不记录
                local orig = initial_actors[act]
                if orig and orig ~= "" then
                    replace_map[act] = orig
                end
            elseif not ignore or restore_override then
                -- 正常替换
                local new_name = trim(res["edit"..idx] or "")
                -- 去掉输入框可能包含的 "[初始]" 前缀
                new_name = new_name:gsub("^%[[^%]]+%]", "")
                if new_name ~= "" and new_name ~= act then
                    replace_map[act] = new_name
                end
            end
        end
        
        -- 显示确认对话框
        if show_confirmation_dialog(replace_map) then
            -- 用户确认了变更，应用更改
            for act, new_name in pairs(replace_map) do
                if new_name ~= act then
                    if new_name ~= "" and not initial_actors[new_name] then
                        initial_actors[new_name] = initial_actors[act] or act
                    end
                    if new_name ~= "" then
                        modified_actors[new_name] = true
                    end
                end
            end
            
            -- 更新忽略状态
            for idx, act in ipairs(actor_list) do
                if res["chk"..idx] then
                    modified_actors[act] = true
                end
            end
            
            -- 批量替换
            for _, i in ipairs(sel) do
                local line = subs[i]
                if line.class=="dialogue" and not line.comment then
                    if replace_map[line.actor] then
                        line.actor = replace_map[line.actor]
                        subs[i] = line
                    end
                end
            end
            
            break -- 完成修改，退出循环
        end
        
        -- 用户取消了确认，返回主对话框继续编辑
        -- 不再重置清空选项，保留用户的选择
    end
end

aegisub.register_macro(script_name, script_description, change_actor_singlecolumn)

