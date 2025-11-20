script_name = "Clip to P1 Drawing"
script_description = "将 \\clip (含矩形) 转换为 \\p1，智能校验与坐标转换"
script_author = "Gemini 3 Pro Preview"
script_version = "5.0"

-- 辅助函数：四舍五入
function round(num, idp)
    local mult = 10^(idp or 3)
    return tostring(math.floor(num * mult + 0.5) / mult)
end

-- 校验函数：检查一行是否可以转换
function check_line_validity(text)
    local clip_pattern = "\\clip%s*%(%s*([^%)]-)%s*%)"
    local clip_content = text:match(clip_pattern)
    
    if not clip_content then
        return false, "未检测到 \\clip 标签"
    end
    
    -- 统计数字数量
    local nums = {}
    for n in clip_content:gmatch("[%-?%d%.]+") do
        table.insert(nums, tonumber(n))
    end
    local num_count = #nums

    -- 判断类型
    if clip_content:find("m") then
        -- === 矢量模式 ===
        -- 3个点 = 6个数字
        if num_count < 6 then
            return false, "矢量路径点数不足 (最少3个点)"
        end
        return true, "Vector"
    else
        -- === 矩形模式 ===
        -- 矩形必须正好是4个数字 (x1, y1, x2, y2)
        if num_count == 4 then
            return true, "Rect"
        else
            return false, "无效的格式 (非标准矩形或矢量)"
        end
    end
end

function clip_to_p1_v5(subs, sel)
    
    -- === 第一阶段：预先扫描 (Pre-Check) ===
    local valid_lines_indices = {} 
    local first_error_reason = ""
    
    for i = 1, #sel do
        local line = subs[sel[i]]
        local is_valid, reason = check_line_validity(line.text)
        
        if is_valid then
            table.insert(valid_lines_indices, sel[i])
        else
            if first_error_reason == "" then first_error_reason = reason end
        end
    end
    
    -- === 第二阶段：交互判断 ===
    if #valid_lines_indices == 0 then
        local err_msg = "无法转换！\n错误原因: " .. first_error_reason
        if #sel > 1 then
            err_msg = "无法转换！\n所选的 " .. #sel .. " 行均不符合要求。\n典型错误: " .. first_error_reason
        end
        aegisub.dialog.display({{class="label", label=err_msg, x=0, y=0, width=1, height=2}}, {"关闭"})
        return 
    end
    
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

    -- === 第三阶段：执行转换 ===
    for _, line_index in ipairs(valid_lines_indices) do
        local line = subs[line_index]
        local text = line.text
        
        local clip_pattern = "\\clip%s*%(%s*([^%)]-)%s*%)"
        local clip_content = text:match(clip_pattern)
        
        -- 准备矢量路径字符串
        local vector_path = ""
        
        if clip_content:find("m") then
            -- [矢量 Clip] 直接截取 m 之后的内容
            local m_index = clip_content:find("m")
            vector_path = clip_content:sub(m_index)
        else
            -- [矩形 Clip] 构造矢量路径
            local c = {}
            for n in clip_content:gmatch("[%-?%d%.]+") do
                table.insert(c, n) -- 存为字符串即可
            end
            -- 矩形逻辑: (x1,y1) -> (x2,y1) -> (x2,y2) -> (x1,y2)
            -- c[1]=x1, c[2]=y1, c[3]=x2, c[4]=y2
            vector_path = string.format("m %s %s l %s %s %s %s %s %s", 
                                        c[1], c[2], c[3], c[2], c[3], c[4], c[1], c[4])
        end
        
        -- 后续通用处理（坐标转换）
        local pos_tag = ""
        local final_path = vector_path
        
        if use_relative then
            -- [相对坐标计算]
            local min_x, min_y = nil, nil
            local coord_counter = 0
            
            -- 扫描所有数字找左上角
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
            
            -- 路径偏移计算
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
    
    aegisub.set_undo_point("Clip to P1 Conversion")
end

aegisub.register_macro(script_name, script_description, clip_to_p1_v5)
