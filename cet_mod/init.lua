-- Judy Neural-Link: in-game chat (CET ImGui) + file bridge to judy_agent.py
-- Files (all relative to this mod folder, CET sandbox):
--   judy_chat_trigger.txt   Lua -> Python : "open the CET overlay for me"
--   judy_chat_request.txt   Lua -> Python : text V typed
--   judy_chat_response.txt  Python -> Lua : "<id>\n<reply>"
--   judy_overlay_state.txt  Lua -> Python : "1" overlay open / "0" closed

local TRIGGER_FILE  = "judy_chat_trigger.txt"
local REQUEST_FILE  = "judy_chat_request.txt"
local RESPONSE_FILE = "judy_chat_response.txt"
local STATE_FILE    = "judy_overlay_state.txt"
local CLOSE_FILE    = "judy_chat_close.txt"
local VRAM_FILE     = "judy_vram_state.txt"

-- Hikaye takibi (judy_story.lua): quest fact'lerini okuyup judy_brain.json'a yazar.
-- Dosya yoksa veya hata verirse mod calismaya devam eder.
local okStory, story = pcall(require, "judy_story")
if not okStory then
    print("[Judy Mod] judy_story.lua yuklenemedi: " .. tostring(story))
    story = nil
end
local function writeStory()
    if story then pcall(story.write_brain) end
end

local apartmentPos = nil

local isNeuralLinkActive = false
local chatOpened = false      -- cuddle already triggered for this visit
local chatActive = false      -- our ImGui window is allowed to draw
local overlayOpen = false
local focusNext = false
local scrollNext = false
local waitingReply = false
local inputText = ""
local history = {}            -- { who = "V" | "Judy", text = "..." }
local lastResponseId = ""
local hudQueue = {}
local hudTimer = 0
local pollTimer = 0

local function writeFile(name, text)
    local f = io.open(name, "w")
    if f then
        f:write(text)
        f:close()
        return true
    end
    return false
end

local function readFile(name)
    local f = io.open(name, "r")
    if not f then return nil end
    local s = f:read("*a")
    f:close()
    return s
end

-- Split a reply into short chunks for the HUD warning message
local function chunkText(text, maxLen)
    local chunks, line = {}, ""
    for word in text:gmatch("%S+") do
        if #line + #word + 1 > maxLen and #line > 0 then
            table.insert(chunks, line)
            line = word
        else
            line = (#line > 0) and (line .. " " .. word) or word
        end
    end
    if #line > 0 then table.insert(chunks, line) end
    return chunks
end

local function setPlayerFrozen(freeze)
    local player = Game.GetPlayer()
    if not player then return end
    -- Temporarily disabled to avoid assumptions
end

local function requestChat(reason)
    do local f = io.open("judy_action_log.txt", "a") if f then f:write(os.date("%H:%M:%S") .. " OPEN_CHAT reason=" .. tostring(reason)  .. "\n") f:close() end end
    chatActive = true
    -- Cuddle'da kutuya hemen odak verme: odaktaki metin kutusu CET'in '-' kisayolunu yutuyor (tus kutuya yaziliyordu).
    -- Odak, overlay gercekten acilinca onOverlayOpen'da verilir.
    focusNext = (reason == "hotkey")
    writeFile(VRAM_FILE, "LOAD_VRAM")
    writeFile(TRIGGER_FILE, "OPEN_CHAT")
    writeStory()
    setPlayerFrozen(true)
end

registerForEvent("onInit", function()
    apartmentPos = Vector4.new(-1377.0, 1275.0, 123.0, 1.0)
    writeFile(STATE_FILE, "0") -- oyun acilirken overlay kapali: bayat "1" kalmasin
    print("[Judy Mod] Neural-Link in-game chat loaded.")
end)

registerForEvent("onOverlayOpen", function()
    do local f = io.open("judy_action_log.txt", "a") if f then f:write(os.date("%H:%M:%S") .. " EVENT onOverlayOpen chatActive=" .. tostring(chatActive) .. "\n") f:close() end end
    overlayOpen = true
    writeFile(STATE_FILE, "1")
    writeStory()
    if chatActive then focusNext = true end
end)

registerForEvent("onOverlayClose", function()
    do local f = io.open("judy_action_log.txt", "a") if f then f:write(os.date("%H:%M:%S") .. " EVENT onOverlayClose chatActive=" .. tostring(chatActive) .. "\n") f:close() end end
    overlayOpen = false
    writeFile(STATE_FILE, "0")
end)
local chatCooldown = 0.0

registerForEvent("onUpdate", function(delta)
    local player = Game.GetPlayer()
    if not player then return end
    if not apartmentPos then
        apartmentPos = Vector4.new(-1377.0, 1275.0, 123.0, 1.0)
    end

    if chatCooldown > 0 then
        chatCooldown = chatCooldown - delta
    end

    -- Check if 'I Really Want to Stay at Your House' (mq055) is active
    -- TODO: Ask user to find the exact fact ID before enabling this check.
    local isHangoutActive = true 
    
    -- Proximity -> NEURAL-LINK message & Dynamic VRAM Hot-Swap
    local dist = Vector4.Distance(player:GetWorldPosition(), apartmentPos)
    if dist < 15.0 and isHangoutActive and not isNeuralLinkActive then
        isNeuralLinkActive = true
        chatOpened = false
        writeFile(VRAM_FILE, "LOAD_VRAM")
        player:SetWarningMessage("NEURAL-LINK ACTIVATED\nJudy Alvarez connected to your frontal cortex.")
    elseif (dist > 20.0 or not isHangoutActive) and isNeuralLinkActive then
        isNeuralLinkActive = false
        chatOpened = false
        chatActive = false
        writeFile(VRAM_FILE, "UNLOAD_VRAM")
        setPlayerFrozen(false)
    end

    -- Poll Judy's reply (twice a second)
    pollTimer = pollTimer + delta
    if pollTimer >= 0.5 then
        pollTimer = 0
        local raw = readFile(RESPONSE_FILE)
        if raw and #raw > 0 then
            local id, body = raw:match("^([^\r\n]*)\r?\n(.*)$")
            if id and body and id ~= lastResponseId then
                lastResponseId = id
                body = body:gsub("^%s+", ""):gsub("%s+$", "")
                if #body > 0 then
                    local last = history[#history]
                    local fallback = "...the link dropped for a sec, V. Say that again?"
                    if last and last.who == "Judy" and last.text == fallback then
                        last.text = body
                    else
                        table.insert(history, { who = "Judy", text = body })
                    end
                    scrollNext = true
                    waitingReply = false
                end
            end
        end
    end

end)

-- Cuddle / interact key -> open chat automatically
local TRIGGER_ACTIONS = {
    ChoiceApply = true, Choice1 = true, Choice2 = true, Choice3 = true, Choice4 = true,
    UI_Apply = true, Interact = true,
}
local seenActions = {}
-- [Cuddle] secimine basilinca hemen acma: Stand'e basmak da ayni ChoiceApply'i uretiyor. Gercek cuddle
-- ancak sonraki secenekler '[Stroke hair]/[Stop cuddling]' olunca baslamis demektir.
local cuddlePendingUntil = 0

-- Which interaction choice is highlighted right now? (so we react to "Cuddle" only)
local currentChoices = {}
local currentChoicesKey = ""
local selectedChoice = 0
local CUDDLE_WORDS = { "cuddle", "saril", "sarıl" }

local function debugLog(line)
    local f = io.open("judy_action_log.txt", "a")
    if f then
        f:write(line .. "\n")
        f:close()
    end
end

local function isCuddleCaption(caption)
    local c = string.lower(tostring(caption or ""))
    
    -- 1. KESİN KARA LİSTE (Asla açılmayacaklar)
    if c:find("stop", 1, true) or 
       c:find("bırak", 1, true) or 
       c:find("interest", 1, true) or 
       c:find("subject", 1, true) or 
       c:find("porn", 1, true) or 
       c:find("braindance", 1, true) then
        return false
    end
    
    -- 2. İZİN LİSTESİ (Kara listeye takılmadıysa ve bunlar varsa aç)
    if c:find("cuddle", 1, true) or 
       c:find("sarıl", 1, true) or 
       c:find("saril", 1, true) then
        return true
    end
    
    return false
end

local dialogsDataLogged = false
local fieldLogCount = 0

local function collectCaptions(hubData)
    -- Accept either a single hub (hub.choices) or a list of hubs (hub.choiceHubs[i].choices)
    local captions = {}
    local function addChoices(choices)
        for i = 1, #choices do
            local c = choices[i]
            local okN, localized = pcall(function() return c.localizedName end)
            local okC, caption = pcall(function() return c.caption end)
            local name = (okN and localized and tostring(localized) ~= "") and tostring(localized)
                or (okC and caption and tostring(caption)) or "?"
            table.insert(captions, name)
            if fieldLogCount < 8 then
                fieldLogCount = fieldLogCount + 1
                debugLog("  choice[" .. i .. "] localizedName=" .. tostring(localized) .. " caption=" .. tostring(caption))
            end
        end
    end
    if hubData.choices then
        addChoices(hubData.choices)
    elseif hubData.choiceHubs then
        local hubs = hubData.choiceHubs
        local pick = nil
        for i = 1, #hubs do
            if hubs[i].active then pick = hubs[i] end
        end
        pick = pick or hubs[1]
        if pick and pick.choices then addChoices(pick.choices) end
    end
    return captions
end

Observe("InteractionUIBase", "OnDialogsData", function(_, value)
    local ok, err = pcall(function()
        local hub = FromVariant(value)
        if not dialogsDataLogged then
            dialogsDataLogged = true
            debugLog("OnDialogsData fired. payload=" .. tostring(hub)
                .. " choices=" .. tostring(hub and hub.choices)
                .. " choiceHubs=" .. tostring(hub and hub.choiceHubs))
        end
        local captions = collectCaptions(hub)
        local key = table.concat(captions, "|")
        if key ~= currentChoicesKey then
            currentChoicesKey = key
            currentChoices = captions
            selectedChoice = 0
            debugLog("CHOICES: " .. key)
            local lk = string.lower(key)
            if cuddlePendingUntil > 0 and not chatOpened and (lk:find("stop cuddling", 1, true) or lk:find("stroke hair", 1, true)) then
                if os.time() <= cuddlePendingUntil then
                    chatOpened = true
                    cuddlePendingUntil = 0
                    requestChat("cuddle-confirmed:" .. key)
                end
            end
        end
    end)
    if not ok then debugLog("OnDialogsData error: " .. tostring(err)) end
end)

Observe("InteractionUIBase", "OnDialogsSelectIndex", function(_, index)
    local ok = pcall(function() selectedChoice = tonumber(index) or 0 end)
    if not ok then selectedChoice = 0 end
end)

local function actionToString(action)
    local ok, name = pcall(function() return Game.NameToString(action:GetName(action)) end)
    if ok and name then return name end
    local ok2, v = pcall(function() return action:GetName(action).value end)
    if ok2 and v then return tostring(v) end
    return "?"
end

Observe("PlayerPuppet", "OnAction", function(this, action)
    if not isNeuralLinkActive then return end

    local actionName = actionToString(action)
    local okT, actionType = pcall(function() return action:GetType(action).value end)
    if not okT then actionType = "?" end

    -- Debug: remember every distinct button action seen near Judy's apartment
    if actionType == "BUTTON_PRESSED" and not seenActions[actionName] then
        seenActions[actionName] = true
        local f = io.open("judy_action_log.txt", "a")
        if f then
            f:write(actionName .. "\n")
            f:close()
        end
    end

    if TRIGGER_ACTIONS[actionName] and actionType == "BUTTON_PRESSED" then
        if chatCooldown > 0 then return end -- Kapatıldıktan sonraki sahte tıklamaları engelle

        local chosenIndex = selectedChoice
        if actionName == "Choice1" then chosenIndex = 0
        elseif actionName == "Choice2" then chosenIndex = 1
        elseif actionName == "Choice3" then chosenIndex = 2
        elseif actionName == "Choice4" then chosenIndex = 3
        end
        
        local caption = currentChoices[chosenIndex + 1]
        debugLog("APPLY action=" .. actionName .. " chosenIndex=" .. tostring(chosenIndex) .. " caption=" .. tostring(caption))
        if not chatOpened and isCuddleCaption(caption) then
            cuddlePendingUntil = os.time() + 15
            debugLog("CUDDLE pending (gercek cuddle icin Stop cuddling secenegi bekleniyor)")
        end
    end
end)

-- Manual fallback hotkey
registerHotkey("judy_chat_hotkey", "Judy: Neural-Link Sohbetini Ac", function()
    requestChat("hotkey")
end)

-- Tani: fact degerlerini CET konsoluna yazar (Bindings sekmesinden bir tus ata)
registerHotkey("judy_story_dump", "Judy: Gorev Fact Degerlerini Yaz (test)", function()
    if story then story.dump() else print("[Judy Mod] judy_story.lua yuklu degil") end
end)

local function sendMessage(text)
    table.insert(history, { who = "V", text = text })
    scrollNext = true
    writeFile(REQUEST_FILE, text)
    waitingReply = true
    inputText = ""
    focusNext = true
end

-- ===== Acilis / kapanis animasyonu (cyberpunk "jack-in") =====
-- Acilis: sari-cyan cizgi ortadan yayilir, cizgi dikeyde acilir, baslik "sifre cozulur", icerik beliriri.
-- Kapanis: ters yon (icerik gider, pencere cizgiye cokup kaybolur). Her iki yonde glitch titremesi.
local ANIM_OPEN  = 0.80
local ANIM_CLOSE = 0.45
local animDir = "idle"      -- "open" | "close" | "idle"
local animT0 = 0
local wasChatActive = false
local baseX, baseY = nil, nil  -- penceranin (surukleme dahil) gercek konumu
local SCRAMBLE = "01ABCDEF#$%&<>/\\|=+*"

local function clamp01(x) if x < 0 then return 0 elseif x > 1 then return 1 end return x end
local function easeOut(x) x = clamp01(x); return 1 - (1 - x) * (1 - x) * (1 - x) end
local function lerp(a, b, t) return a + (b - a) * t end

-- final metnin ilk 'v' orani gercek, gerisi rastgele karakter
local function scrambleText(final, v)
    local n = #final
    local keep = math.floor(n * clamp01(v))
    local out = {}
    for i = 1, n do
        local ch = final:sub(i, i)
        if i <= keep or ch == " " then
            out[i] = ch
        else
            local r = math.random(1, #SCRAMBLE)
            out[i] = SCRAMBLE:sub(r, r)
        end
    end
    return table.concat(out)
end

registerForEvent("onDraw", function()
    -- Durum (overlayOpen / judy_overlay_state.txt) sadece onOverlayOpen/onOverlayClose olaylarindan gelir.
    -- onDraw overlay kapaliyken de calisir; buradan 'acik' demek durum dosyasini yanlis '1' yapiyordu.
    local now = os.clock()
    if chatActive and not wasChatActive then animDir = "open"; animT0 = now end
    if (not chatActive) and wasChatActive then animDir = "close"; animT0 = now end
    wasChatActive = chatActive
    if (not chatActive) and animDir ~= "close" then return end

    -- v: gorunurluk 0..1
    local v = 1.0
    if animDir == "open" then
        local p = (now - animT0) / ANIM_OPEN
        if p >= 1 then animDir = "idle"; v = 1.0 else v = clamp01(p) end
    elseif animDir == "close" then
        local p = (now - animT0) / ANIM_CLOSE
        if p >= 1 then animDir = "idle"; return end
        v = 1.0 - clamp01(p)
    end
    local animating = (animDir ~= "idle")

    local sw, sh = 1920, 1080
    local ok, w1, h1 = pcall(ImGui.GetDisplaySize)
    if ok and w1 and h1 then sw, sh = w1, h1 end

    local w, h = 650, 400
    if not baseX then baseX, baseY = sw - w - 40, (sh - h) / 2 end

    local curW, curH = w, h
    if animating then
        -- 0..0.3: cizgi yatayda yayilir; 0.3..1: cizgi dikeyde acilir
        if v < 0.3 then
            curW = math.max(4, w * easeOut(v / 0.3))
            curH = 3
        else
            curH = math.max(3, lerp(3, h, easeOut((v - 0.3) / 0.7)))
        end
        local jitter = 0
        if math.random() < 0.6 then jitter = (math.random() * 2 - 1) * 12 * (1 - v) end
        ImGui.SetNextWindowPos(baseX + (w - curW) / 2 + jitter, baseY + (h - curH) / 2, ImGuiCond.Always)
        ImGui.SetNextWindowSize(curW, curH, ImGuiCond.Always)
    else
        ImGui.SetNextWindowPos(sw - w - 40, (sh - h) / 2, ImGuiCond.FirstUseEver)
        ImGui.SetNextWindowSize(w, h, ImGuiCond.FirstUseEver)
    end

    -- SİMSİYAH, ŞEFFAF VE KESKİN HATLARA SAHİP (NO TITLE BAR) PENCERE
    local bgA = animating and (0.85 * clamp01((v - 0.15) / 0.5)) or 0.85
    ImGui.PushStyleColor(ImGuiCol.WindowBg, 0.01, 0.02, 0.02, bgA)
    -- animasyon sirasinda sari -> cyan parlayan kenar cizgisi
    if animating then
        ImGui.PushStyleColor(ImGuiCol.Border, lerp(0.99, 0.2, v), lerp(0.93, 0.9, v), lerp(0.04, 0.9, v), 1.0)
    else
        ImGui.PushStyleColor(ImGuiCol.Border, 0.0, 0.0, 0.0, 0.0)
    end
    ImGui.PushStyleColor(ImGuiCol.Separator, 0.2, 0.8, 0.8, 0.5)
    local nVars = 0
    ImGui.PushStyleVar(ImGuiStyleVar.WindowRounding, 0.0); nVars = nVars + 1
    ImGui.PushStyleVar(ImGuiStyleVar.WindowBorderSize, animating and 1.0 or 0.0); nVars = nVars + 1
    if animating then
        -- ince cizgi icin ImGui'nin min pencere boyutunu (32x32) kucult; yoksa cizgi gorunumu bozulur
        if pcall(ImGui.PushStyleVar, ImGuiStyleVar.WindowMinSize, 2.0, 2.0) then nVars = nVars + 1 end
        -- icerik/yazi/kutu icin ortak solma (destek yoksa sessizce atlanir)
        local bodyA = clamp01((v - 0.55) / 0.45)
        if pcall(ImGui.PushStyleVar, ImGuiStyleVar.Alpha, math.max(0.05, bodyA)) then nVars = nVars + 1 end
    end

    -- BAŞLIK ÇUBUĞUNU (TITLE BAR) TAMAMEN GİZLİYORUZ (Ama sürüklenebilir olması için NoMove'u SİLDİK)
    local flags = ImGuiWindowFlags.NoTitleBar + ImGuiWindowFlags.NoResize + ImGuiWindowFlags.NoCollapse + ImGuiWindowFlags.NoNav
    if animating then flags = flags + ImGuiWindowFlags.NoScrollbar + ImGuiWindowFlags.NoScrollWithMouse + ImGuiWindowFlags.NoMove end
    if ImGui.Begin("NeuralLinkUI", flags) then
        if not animating then
            -- kullanici pencereyi surukledi mi? Bir sonraki animasyon ayni yerde baslasin.
            local okp, px, py = pcall(ImGui.GetWindowPos)
            if okp and px and py then baseX, baseY = px, py end
        end
        
        -- ÖZEL BAŞLIK (Resimdeki gibi)
        local headerText = "[ NEURAL-LINK: JUDY_ALVAREZ_CONNECTION_ACTIVE ]"
        if animating then headerText = scrambleText(headerText, (v - 0.25) / 0.6) end
        ImGui.TextColored(0.4, 0.8, 0.8, 1.0, headerText)
        ImGui.Separator()
        ImGui.Spacing()

        if animating and v < 0.55 then
            -- henuz icerik yok: yukleme cubugu
            local n = math.floor(clamp01(v / 0.55) * 20)
            ImGui.TextColored(0.99, 0.93, 0.04, 1.0, "> JACKING IN [" .. string.rep("#", n) .. string.rep(".", 20 - n) .. "]")
        else
        -- SOHBET GEÇMİŞİ ALANI
        ImGui.BeginChild("judy_history", w - 20, h - 100, false)
        ImGui.PushTextWrapPos(w - 30)
        for _, m in ipairs(history) do
            if m.who == "Judy" then
                ImGui.TextColored(0.4, 1.0, 0.6, 1.0, "JUDY: " .. m.text)
            else
                ImGui.TextColored(0.7, 0.7, 0.7, 1.0, "V: " .. m.text)
            end
            ImGui.Spacing()
        end
        ImGui.PopTextWrapPos()

        if scrollNext then
            ImGui.SetScrollHereY(1.0)
            scrollNext = false
        end
        ImGui.EndChild()

        ImGui.Separator()
        ImGui.Spacing()

        -- YÜKLEME EFEKTİ VEYA GİRİŞ ALANI
        if waitingReply then
            -- Resimdeki API maskeleme animasyonu
            local t = math.floor(os.clock() * 3)
            local dots = string.rep(".", t % 4)
            ImGui.TextColored(0.4, 1.0, 0.6, 1.0, "[ > NEURAL-LINK ENCRYPTING" .. dots .. " ]")
            
            -- Sağ tarafta sahte hacker logları
            ImGui.SameLine(w - 180)
            local hex1 = string.format("%04X", math.random(0, 65535))
            local hex2 = string.format("%04X", math.random(0, 65535))
            ImGui.TextColored(0.3, 0.6, 0.6, 1.0, "BYPASSING ICE...\n0x" .. hex1 .. "  0x" .. hex2)
        else
            -- NORMAL GİRİŞ ALANI (Görünmez arkaplan)
            ImGui.PushStyleColor(ImGuiCol.FrameBg, 0.0, 0.0, 0.0, 0.3)
            ImGui.PushStyleColor(ImGuiCol.Text, 0.4, 1.0, 0.6, 1.0)
            ImGui.PushItemWidth(w - 100)
            
            if focusNext then
                ImGui.SetKeyboardFocusHere()
                focusNext = false
            end
            
            local txt, entered = ImGui.InputText("##judyinput", inputText, 256, ImGuiInputTextFlags.EnterReturnsTrue)
            inputText = txt
            if entered and txt ~= "" then
                sendMessage(txt)
            end
            ImGui.PopItemWidth()
            ImGui.PopStyleColor(2)

            -- KAPAT BUTONU (Minimalist)
            ImGui.SameLine()
            ImGui.PushStyleColor(ImGuiCol.Button, 0.1, 0.1, 0.1, 0.8)
            ImGui.PushStyleColor(ImGuiCol.Text, 0.8, 0.2, 0.2, 1.0)
            if ImGui.Button("[X] Kapat", 80, 0) then
                chatActive = false
                chatOpened = false
                chatCooldown = 2.0
                inputText = ""
                writeFile(CLOSE_FILE, "1")
                setPlayerFrozen(false)
            end
            ImGui.PopStyleColor(2)
        end
        end -- animasyon/icerik
    end
    ImGui.End()
    
    ImGui.PopStyleVar(nVars)
    ImGui.PopStyleColor(3)
end)
