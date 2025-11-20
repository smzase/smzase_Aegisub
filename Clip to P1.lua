script_name = "Clip to P1 Drawing"
script_description = "将 \\clip 转换为 \\p1，先校验后弹窗"
script_author = "Assistant"
script_version = "4.0"

-- 辅助函数：四舍五入
function round(num, idp)
    local mult = 10^(idp or 3)
    return tostring(math.floor(num * mult + 0.5) / mult)
end

-- 校验函数：检查一行是否可以转换
-- 返回: is_valid (bool), reason (string), clip_data (table/string)
function check_line_validity(text)
    local clip_pattern = "\\clip%s*%(%s*([^%)]-)%s*%)"
    local clip_content = text:match(clip_pattern)
    
    if not clip_content then
        return false, "未检测到 \\clip 标签"
    end
    
    -- 检查是否包含绘图指令 m
    if not clip_content:find("m") then
        return false, "非矢量绘图 (未找到 m 指令)"
    end
    
    -- 统计坐标点数量
    local num_count = 0
    for _ in clip_content:gmatch("[%-?%d%.]+") do
        num_count = num_count + 1
    end
    
    -- 3个点 = 2(x,y) * 3 = 6个数字
    if num_count < 6 then
        return false, "坐标点不足 (最少需要3个点)"
    end
    
    return true, "OK", clip_content
end

function clip_to_p1_v4(subs, sel)
    
    -- === 第一阶段：预先扫描 (Pre-Check) ===
    local valid_lines_indices = {} -- 记录合格的行号
    local first_error_reason = ""  -- 记录第一个错误的理由
    
    for i = 1, #sel do
        local line = subs[sel[i]]
        local is_valid, reason, _ = check_line_validity(line.text)
        
        if is_valid then
            table.insert(valid_lines_indices, sel[i])
        else
            if first_error_reason == "" then first_error_reason = reason end
        end
    end
    
    -- === 第二阶段：根据扫描结果决定流程 ===
    
    -- 情况A：没有任何一行是合格的
    if #valid_lines_indices == 0 then
        -- 直接报错，不弹出配置窗口
        local err_msg = "无法转换！\n错误原因: " .. first_error_reason
        if #sel > 1 then
            err_msg = "无法转换！\n所选的 " .. #sel .. " 行均不符合要求。\n典型错误: " .. first_error_reason
        end
        aegisub.dialog.display({{class="label", label=err_msg, x=0, y=0, width=1, height=2}}, {"关闭"})
        return -- 结束脚本
    end
    
    -- 情况B：有合格的行，弹出配置窗口
    local dialog_config = {
        {class="label", label="检测到 " .. #valid_lines_indices .. " 行可转换，请选择模式:", x=0, y=0, width=1},
        {class="dropdown", name="mode", 
         items={"计算相对坐标", "绝对坐标"}, 
         value="计算相对坐标", x=1, y=0, width=1, 
         hint="相对: 自动计算左上角偏移 (推荐)"}
    }
    
    local buttons, result = aegisub.dialog.display(dialog_config, {"开始转换", "取消"})
    
    if buttons ~= "开始转换" then return end
    
    local use_relative = (result.mode == "计算相对坐标")

    -- === 第三阶段：执行转换 (只处理 valid_lines_indices) ===
    -- 注意：这里我们只遍历记录下来的“合格行号”
    for _, line_index in ipairs(valid_lines_indices) do
        local line = subs[line_index]
        local text = line.text
        
        -- 这里不需要再校验了，直接提取使用
        -- 重新匹配一次内容
        local clip_pattern = "\\clip%s*%(%s*([^%)]-)%s*%)"
        local clip_content = text:match(clip_pattern)
        local m_index = clip_content:find("m")
        local vector_path = clip_content:sub(m_index)
        
        local pos_tag = ""
        local final_path = vector_path
        
        if use_relative then
            -- [相对坐标计算]
            local min_x, min_y = nil, nil
            local coord_counter = 0
            
            -- 找左上角
            for num_str in vector_path:gmatch("[%-?%d%.]+") do
                local val = tonumber(num_str)
                if val then
                    coord_counter = coord_counter + 1
                    if coord_counter % 2 == 1 then
                        if not min_x or val < min_x then min_x = val end
                    else
                        if not min_y or val < min_y then min_y = val end
                    end
                end
            end
            
            min_x = min_x or 0
            min_y = min_y or 0
            
            pos_tag = string.format("\\pos(%s,%s)", round(min_x), round(min_y))
            
            -- 坐标偏移
            coord_counter = 0
            final_path = vector_path:gsub("([%-?%d%.]+)", function(n)
                local val = tonumber(n)
                coord_counter = coord_counter + 1
                if coord_counter % 2 == 1 then
                    return round(val - min_x)
                else
                    return round(val - min_y)
                end
            end)
        else
            -- [绝对坐标]
            pos_tag = "\\pos(0,0)"
            final_path = vector_path
        end
        
        -- 标签重组
        text = (text:gsub(clip_pattern, ""))
        local tag_block = text:match("^{[^}]*}")
        if tag_block then
            tag_block = tag_block:gsub("}$", "")
        else
            tag_block = "{"
        end
        
        line.text = tag_block .. "\\an7" .. pos_tag .. "\\p1}" .. final_path
        line.text = line.text:gsub("^{{", "{")
        
        subs[line_index] = line
    end
    
    -- 如果有部分行被跳过（选了5行，只有3行有效），可以在这里提示，或者保持安静
    -- 为了体验顺滑，只要有成功的，通常就不弹窗了，除非你希望看到统计
    
    aegisub.set_undo_point("Clip to P1 Conversion")
end

aegisub.register_macro(script_name, script_description, clip_to_p1_v4)