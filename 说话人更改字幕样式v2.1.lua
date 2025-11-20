-- 脚本信息
local tr = aegisub.gettext
script_name = "说话人修改样式和清除颜色v2.1"
script_description = "根据说话人批量修改字幕样式，并可选清除特定颜色标签"
script_author = "Claude3.5"
script_version = "2.1"  -- 版本号更新

-- 配置文件
local config_file = "actor_style_changer_config.conf"

-- 将ASS颜色值转换为Aegisub颜色预览格式
local function ass_color_to_preview(color_str)
    if not color_str then return "&H000000&" end
    -- 格式转换: &HBBGGRR -> &HAABBGGRR (添加透明度)
    return "&HFF" .. color_str:sub(3, 8)  -- FF表示完全不透明
end

-- 加载配置
local function load_config()
    local file = io.open(config_file, "r")
    local cfg = {}
    if file then
        for line in file:lines() do
            local key, value = line:match("^(.-)=(.-)$")
            if key and value then cfg[key] = value end
        end
        file:close()
    end
    return cfg
end

-- 保存配置
local function save_config(cfg)
    local file = io.open(config_file, "w")
    if file then
        for k, v in pairs(cfg) do
            file:write(k .. "=" .. v .. "\n")
        end
        file:close()
    end
end

-- 获取所有说话人
local function get_all_actors(subs)
    local actors = {}
    for i = 1, #subs do
        if subs[i].class == "dialogue" and subs[i].actor ~= "" then
            actors[subs[i].actor] = true
        end
    end
    local actor_list = {}
    for actor, _ in pairs(actors) do table.insert(actor_list, actor) end
    table.sort(actor_list)
    return actor_list
end

-- 获取所有样式
local function get_all_styles(subs)
    local styles = {}
    for i = 1, #subs do
        if subs[i].class == "style" then
            table.insert(styles, subs[i].name)
        end
    end
    table.sort(styles)
    return styles
end

-- 获取样式和颜色信息
local function get_styles_and_colors(subs, actor, lines)
    local styles = {}
    for _, i in ipairs(lines) do
        local line = subs[i]
        if line.class == "dialogue" and line.actor == actor then
            if not styles[line.style] then 
                styles[line.style] = {
                    ["1c"] = {},
                    ["2c"] = {},
                    ["3c"] = {},
                    ["4c"] = {}
                }
            end
            
            -- 检查 \1c 和 \c
            for color in line.text:gmatch("\\1c&H(%x+)&") do
                styles[line.style]["1c"][color] = true
            end
            for color in line.text:gmatch("\\c&H(%x+)&") do
                styles[line.style]["1c"][color] = true
            end
            
            -- 检查 \2c
            for color in line.text:gmatch("\\2c&H(%x+)&") do
                styles[line.style]["2c"][color] = true
            end
            
            -- 检查 \3c
            for color in line.text:gmatch("\\3c&H(%x+)&") do
                styles[line.style]["3c"][color] = true
            end
            
            -- 检查 \4c
            for color in line.text:gmatch("\\4c&H(%x+)&") do
                styles[line.style]["4c"][color] = true
            end
        end
    end
    return styles
end

-- 主函数
function actor_style_changer(subs, sel)
    local cfg = load_config()

    -- 获取所有说话人和样式
    local actors = get_all_actors(subs)
    local styles = get_all_styles(subs)

    if #actors == 0 then 
        aegisub.dialog.display({{class="label", label="未找到任何说话人！"}}, {"确定"})
        aegisub.cancel() 
    end

    -- 第一个对话框
    local dialog_config = {
        {class="label", label="选择要修改的说话人：", x=0, y=0},
        {class="dropdown", name="actor", items=actors, value=actors[1], x=1, y=0},
        {class="checkbox", name="change_all", label="更改所有行（否则仅更改所选行）", value=(cfg.change_all=="true"), x=0, y=1, width=2},
    }

    local pressed, res = aegisub.dialog.display(dialog_config, {"下一步", "取消"})
    if pressed ~= "下一步" then aegisub.cancel() end

    -- 确定要处理的行
    local lines_to_detect = {}
    if res.change_all then
        for i = 1, #subs do
            if subs[i].class == "dialogue" then table.insert(lines_to_detect, i) end
        end
    else
        if #sel == 0 then
            aegisub.dialog.display({{class="label", label="未选择任何行！请先选择字幕行。"}}, {"确定"})
            aegisub.cancel()
        end
        lines_to_detect = sel
    end

    -- 获取样式和颜色信息
    local actor_styles = get_styles_and_colors(subs, res.actor, lines_to_detect)
    local style_names = {}
    for sty,_ in pairs(actor_styles) do table.insert(style_names, sty) end
    table.sort(style_names)

    if #style_names == 0 then
        aegisub.dialog.display({{class="label", label="所选范围内该说话人没有使用任何样式！"}}, {"确定"})
        aegisub.cancel()
    end

    -- 构建第二个对话框
    local style_dialog = {}
    local y_pos = 0
    
    -- 添加当前说话人信息
    table.insert(style_dialog, {
        class="label",
        label="当前修改说话人: " .. res.actor,
        x=0, y=y_pos,
        width=3
    })
    y_pos = y_pos + 1
    
    for _, old_style in ipairs(style_names) do
        -- 样式选择
        table.insert(style_dialog, {
            class="label", 
            label="将 ["..old_style.."] 改为：", 
            x=0, y=y_pos
        })
        table.insert(style_dialog, {
            class="dropdown", 
            name="new_style_"..old_style, 
            items=styles, 
            value=old_style, 
            x=1, y=y_pos,
            width=2
        })
        y_pos = y_pos + 1

        -- 颜色选择，每种颜色类型占一行，每个颜色单独一个选择框
        for _, ct in ipairs({"1c","2c","3c","4c"}) do
            local colors = actor_styles[old_style][ct]
            if next(colors) then
                -- 添加颜色类型标签
                local label_text = ct == "1c" and "\\c:" or "\\"..ct..":"
                table.insert(style_dialog, {
                    class="label",
                    label=label_text,
                    x=1,
                    y=y_pos
                })
                
                -- 为每个颜色添加单独的选择框和颜色预览
                local x_pos = 2
                local color_array = {}
                for color, _ in pairs(colors) do
                    table.insert(color_array, color)
                end
                table.sort(color_array) -- 排序以保持一致的顺序
                
                for _, color in ipairs(color_array) do
                    -- 添加颜色预览控件
                    table.insert(style_dialog, {
                        class="color",
                        name="preview_"..old_style.."_"..ct.."_"..color,
                        value=ass_color_to_preview(color),
                        x=x_pos,
                        y=y_pos
                    })
                    x_pos = x_pos + 1
                    
                    -- 添加清除复选框
                    table.insert(style_dialog, {
                        class="checkbox",
                        name="clear_"..old_style.."_"..ct.."_"..color,
                        label="清除 &H"..color.."&",
                        value=false,
                        x=x_pos,
                        y=y_pos
                    })
                    x_pos = x_pos + 1
                end
                y_pos = y_pos + 1
            end
        end
        -- 添加一个空行，分隔不同的样式
        y_pos = y_pos + 1
    end

    -- 显示第二个对话框
    local pressed2, res2 = aegisub.dialog.display(style_dialog, {"确定", "取消"})
    if pressed2 ~= "确定" then aegisub.cancel() end

    -- 处理字幕行
    for _, i in ipairs(lines_to_detect) do
        local line = subs[i]
        local old_style = line.style
        if line.actor == res.actor and actor_styles[old_style] then
            -- 更改样式
            line.style = res2["new_style_"..old_style]

            -- 清除选中的颜色标签
            local text = line.text
            for _, ct in ipairs({"1c","2c","3c","4c"}) do
                local colors = actor_styles[old_style][ct]
                for color, _ in pairs(colors) do
                    if res2["clear_"..old_style.."_"..ct.."_"..color] then
                        if ct == "1c" then
                            text = text:gsub("\\1c&H"..color.."&", "")
                            text = text:gsub("\\c&H"..color.."&", "")
                        else
                            text = text:gsub("\\"..ct.."&H"..color.."&", "")
                        end
                    end
                end
            end

            -- 清除空的花括号
            text = text:gsub("{[^}]*}", function(tag)
                local content = tag:sub(2, -2)
                if content:match("^\\?%s*$") then
                    return ""
                end
                return tag
            end)

            line.text = text
            subs[i] = line
        end
    end

    -- 保存配置
    cfg.change_all = tostring(res.change_all)
    save_config(cfg)

    aegisub.set_undo_point(script_name)
end

-- 注册宏
aegisub.register_macro(script_name, script_description, actor_style_changer)

