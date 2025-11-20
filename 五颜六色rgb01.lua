-- Aegisub Gradient Color Script
-- Creates text with gradient colors from left to right or right to left
-- Supports 2-4 colors for gradient transitions

script_name = "Gradient Color"
script_description = "Apply gradient colors to text"
script_author = "Claude3.7"
script_version = "1.0"

-- Function to convert RGB to ASS color format (&HBBGGRR&)
function rgb_to_ass(r, g, b)
    return string.format("&H%02X%02X%02X&", b, g, r)
end

-- Function to convert ASS color to RGB
function ass_to_rgb(ass_color)
    -- Remove the &H and & parts
    local color = ass_color:gsub("&H", ""):gsub("&", "")
    -- Extract BGR values (ASS format is &HBBGGRR&)
    local b = tonumber(color:sub(1, 2), 16) or 0
    local g = tonumber(color:sub(3, 4), 16) or 0
    local r = tonumber(color:sub(5, 6), 16) or 0
    return r, g, b
end

-- Function to interpolate between two colors
function interpolate_color(color1, color2, ratio)
    local r1, g1, b1 = ass_to_rgb(color1)
    local r2, g2, b2 = ass_to_rgb(color2)
    
    local r = r1 + (r2 - r1) * ratio
    local g = g1 + (g2 - g1) * ratio
    local b = b1 + (b2 - b1) * ratio
    
    return rgb_to_ass(math.floor(r + 0.5), math.floor(g + 0.5), math.floor(b + 0.5))
end

-- Function to create gradient colors between multiple colors
function create_gradient_colors(colors, num_steps)
    if #colors < 2 then return {colors[1]} end
    
    local result = {}
    local sections = #colors - 1
    local steps_per_section = math.floor(num_steps / sections)
    local remaining_steps = num_steps - (steps_per_section * sections)
    
    local current_step = 0
    for i = 1, sections do
        local section_steps = steps_per_section
        if i <= remaining_steps then
            section_steps = section_steps + 1
        end
        
        for j = 0, section_steps - 1 do
            if i == sections and j == section_steps - 1 then
                -- Ensure the last color is exactly the final color
                table.insert(result, colors[#colors])
            else
                local ratio = j / section_steps
                local interpolated = interpolate_color(colors[i], colors[i+1], ratio)
                table.insert(result, interpolated)
            end
            current_step = current_step + 1
            if current_step >= num_steps then break end
        end
        if current_step >= num_steps then break end
    end
    
    -- If we didn't fill all steps (due to rounding), add the last color
    while #result < num_steps do
        table.insert(result, colors[#colors])
    end
    
    return result
end

-- Function to split UTF-8 string into individual characters
function utf8_chars(text)
    local chars = {}
    local pos = 1
    local bytes = #text
    
    while pos <= bytes do
        local b = text:byte(pos)
        local char_len = 1
        
        if b >= 128 then
            if b >= 240 then
                char_len = 4
            elseif b >= 224 then
                char_len = 3
            elseif b >= 192 then
                char_len = 2
            end
        end
        
        table.insert(chars, text:sub(pos, pos + char_len - 1))
        pos = pos + char_len
    end
    
    return chars
end

-- Main function to apply gradient
function apply_gradient(subtitles, selected_lines, active_line)
    local dialog_config = {
        {
            class = "label",
            x = 0, y = 0,
            label = "Start Color (format: &HBBGGRR&):"
        },
        {
            class = "edit",
            name = "color1",
            x = 1, y = 0,
            value = "&HFFBE0E&"  -- Yellow from example
        },
        {
            class = "label",
            x = 0, y = 1,
            label = "End Color (format: &HBBGGRR&):"
        },
        {
            class = "edit",
            name = "color2",
            x = 1, y = 1,
            value = "&HB342FF&"  -- Purple from example
        },
        {
            class = "label",
            x = 0, y = 2,
            label = "Optional Middle Color 1:"
        },
        {
            class = "edit",
            name = "color3",
            x = 1, y = 2,
            value = ""
        },
        {
            class = "label",
            x = 0, y = 3,
            label = "Optional Middle Color 2:"
        },
        {
            class = "edit",
            name = "color4",
            x = 1, y = 3,
            value = ""
        },
        {
            class = "dropdown",
            name = "direction",
            x = 0, y = 4, width = 2,
            items = {"Left to Right", "Right to Left"},
            value = "Left to Right"
        }
    }
    
    local buttons = {"Apply", "Cancel"}
    local pressed, results = aegisub.dialog.display(dialog_config, buttons)
    
    if pressed == "Cancel" then
        return
    end
    
    -- Build color array with selected colors
    local colors = {results.color1, results.color2}
    if results.color3 and results.color3 ~= "" then
        table.insert(colors, 2, results.color3)
    end
    if results.color4 and results.color4 ~= "" then
        if #colors >= 3 then
            table.insert(colors, 3, results.color4)
        else
            table.insert(colors, 2, results.color4)
        end
    end
    
    local is_right_to_left = results.direction == "Right to Left"
    
    for _, idx in ipairs(selected_lines) do
        local line = subtitles[idx]
        
        -- Extract existing style tags and text content
        local text = line.text
        local prefix = ""
        local clean_text = text
        
        -- Find all style override blocks
        local style_blocks = {}
        for block in text:gmatch("{[^}]*}") do
            table.insert(style_blocks, block)
        end
        
        -- If we have style blocks, extract the first one as prefix
        -- and remove all style blocks from the text
        if #style_blocks > 0 then
            prefix = style_blocks[1]
            -- Remove color tags from the prefix
            prefix = prefix:gsub("\\c&H%x+&", "")
            -- Fix empty braces
            if prefix == "{}" then prefix = "{\\}" end
            
            -- Remove all style blocks to get clean text
            clean_text = text:gsub("{[^}]*}", "")
        end
        
        -- Split text into characters
        local chars = utf8_chars(clean_text)
        local num_chars = #chars
        
        if num_chars == 0 then
            -- No characters to process
            goto continue
        end
        
        local gradient_colors = create_gradient_colors(colors, num_chars)
        
        -- If right to left, reverse the colors
        if is_right_to_left then
            local reversed = {}
            for i = #gradient_colors, 1, -1 do
                table.insert(reversed, gradient_colors[i])
            end
            gradient_colors = reversed
        end
        
        -- Combine the characters with their color tags
        local result = ""
        for i, char in ipairs(chars) do
            result = result .. "{\\c" .. gradient_colors[i] .. "}" .. char
        end
        
        -- Add prefix with style tags back
        if prefix ~= "" and prefix ~= "{\\}" then
            line.text = prefix .. result
        else
            line.text = result
        end
        
        subtitles[idx] = line
        
        ::continue::
    end
    
    aegisub.set_undo_point("Apply Gradient Colors")
end

-- Register the macro
aegisub.register_macro(script_name, script_description, apply_gradient)

