--[[
 檔案名稱：kage_autoRuby.lua
 腳本製作：影kage
 自動上小字特效(非卡拉OK)
 
 *請將文件放到Aegisub\automation\autoload資料夾內方可使用
 *每次調完參數請到[自動化腳本管理]去[重新整理]
 *套用的字幕樣式

 Copyright (c) 2013-2014,Kage Maboroshi/TUcaptions, All rights reserved. 
 
 this script is a modifer of kage ruby sciprt
 
]]

--以下資料請不要亂動--

require "karaskel"

script_name = "自動上小字特效(可批量)"
script_description = "自動上小字特效，使用方法(文字,小字)"
script_author = "Kage Maboroshi(影kage)"
script_modifer_author = "KiNen";
script_modifer_author2 = "saiyaku";
script_version = 1.0

--參數設定--
rubypadding = -5 --小字間距
rubyscale = 0 --小字縮放比例 (設定為0時，不添加 \fs 標籤)
fstyle = "Text ENG" --小字style

meta = nil;
styles = nil;


function Ruby(subs, sel)
	meta, styles = karaskel.collect_head(subs);
    local n = 0
	local x = 0	
	for z, i in ipairs(sel) do
		n = n+x
		local l = subs[i+n]
		x = processline(subs,l,i+n);
		if x == 1 then 
			x = 0;
		else	
			local l2 = subs[i+n];
			l2.comment = true;			
			subs[i+n] = l2; 
		end
	end
	aegisub.set_undo_point(script_name) 
end

function processline(subs,line,li)
    line.comment = false;
	local originline = table.copy(line);

	local ktag="{\\k0}";
	local stylefs = styles[ line.style ].fontsize;
	local rubbyfs = stylefs * rubyscale;
	line.text = string.gsub(line.text,"%((.-),(.-)%)",ktag.."%1".."|".."%2"..ktag);
    local vl = table.copy(line);
	karaskel.preproc_line(subs, meta, styles, vl);
	
	if vl.furi.n ~= 0 then 
		originline.text = string.gsub(originline.text,"%((.-),(.-)%)","%1");
		originline.text = originline.text; --string.format("\{\\pos(%d,%d)\}",vl.x,vl.y)..
		subs.insert(li+1,originline);
	end
	for i = 1, vl.furi.n do
		local fl = table.copy(line)
		local rlx = vl.left + vl.kara[vl.furi[i].i].center;
		local rly = vl.top - rubypadding; -- an2 如果an5   local rly = vl.top - rubbyfs/2 - rubypadding;
		fl.style = fstyle;
		
		-- 判斷 rubyscale 是否為 0，如果為 0 則不添加 \fs 標籤
		if rubyscale == 0 then
			fl.text = string.format("{\\pos(%d,%d)}%s",rlx,rly,vl.furi[i].text);
			--fl.text = string.format("{\\an5\\pos(%d,%d)}%s",rlx,rly,vl.furi[i].text);
		else
			fl.text = string.format("{\\fs%d\\pos(%d,%d)}%s",rubbyfs,rlx,rly,vl.furi[i].text);
			--fl.text = string.format("{\\an5\\fs%d\\pos(%d,%d)}%s",rubbyfs,rlx,rly,vl.furi[i].text);
		end
		
		subs.insert(li+1+i,fl);
	end
	return vl.furi.n + 1;
end


aegisub.register_macro(script_name, script_description, Ruby)