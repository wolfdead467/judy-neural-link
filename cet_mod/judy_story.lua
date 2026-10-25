-- ==========================================
-- JUDY NEURAL-LINK: HIKAYE TAKIBI (quest fact'leri)
-- Fact adlari FactFinder.reds dokumunden (oyun surumu 2.1, Discord listesi).
-- init.lua icinden kullanilir:  local story = require("judy_story")
-- ==========================================
-- Lua sadece "bu fact >= 1 mi" bilgisini id olarak yazar.
-- Cumleler, sira ve "Judy bilir mi" bilgisi Python'da (judy_agent.py).

local Story = {}

-- id -> fact adi
local FACTS = {
    { id = "heist",        fact = "q005_done" },            -- The Heist bitti (Jackie oldu)
    { id = "heroes",       fact = "sq018_done" },           -- Heroes (Jackie'nin cenazesi)
    { id = "both_sides",   fact = "sq026_01_done" },        -- Both Sides, Now (Evelyn)
    { id = "ex_factor",    fact = "sq026_02_done" },        -- Ex-Factor
    { id = "pizza",        fact = "sq026_03_done" },        -- Talkin' 'bout a Revolution
    { id = "pisces",       fact = "sq026_04_done" },        -- Pisces
    { id = "maiko_dead",   fact = "sq026_maiko_dead" },     -- Maiko oldu (oyunda DOGRULANDI: oldurunce 1)
    -- Pisces dallari. DOGRULANMADI: sadece "Maiko'yu oldurdum" kaydinda gozlendi. Baska dallari
    -- dogrulayana kadar RAG_branches.json'daki ilgili parcalar enabled:false.
    { id = "maiko_boss",   fact = "sq026_maiko_boss" },     -- Maiko Clouds'un patronu oldu (tahmin; oldurme kaydinda -1)
    { id = "side_judy",    fact = "sq026_side_judy" },      -- V Judy'nin tarafini tuttu (tahmin)
    { id = "side_maiko",   fact = "sq026_side_maiko" },     -- V Maiko'nun tarafini tuttu (tahmin)
    { id = "maiko_money",  fact = "sq026_13_maiko_money" }, -- Maiko'nun parasi alindi (tahmin)
    { id = "judy_lover",   fact = "sq030_judy_lover" },     -- V ve Judy sevgili
    { id = "judy_friend",  fact = "sq030_friendship" },     -- V ve Judy sadece arkadas
    { id = "sinnerman",    fact = "sq023_done" },           -- Sinnerman (V'nin kendi isi, Judy bilmez)
}

-- Sadece tani dokumunde gosterilir, dosyaya yazilmaz. Anlamlarini dogrulamak icin.
-- hiromi_dead (sq026_hiromi_dead) yazilanlardan CIKARILDI: Judy tarafinda Hiromi olse de 0 cikti (adi yaniltici).
local DIAG = {
    "sq026_done", "sq026_hiromi_dead", "sq026_refused_maiko", "sq026_refused_hiromi",
    "sq026_no_kill", "sq026_judy_fan", "sq026_corpo_V", "sq026_muscle_V",
    "sq026_13_maiko_money_extra", "sq026_maiko_security", "sq026_hiromi_combat", "sq026_hiromi_loot",
    "sq030_done", "sq030_11_romance", "sq030_refused", "sq030_01_judy_sad", "q115_judy_romance_chosen",
}

-- Fact degerini sayi olarak dondurur, okunamazsa nil.
local function fact_value(qs, name)
    local ok, v = pcall(function() return qs:GetFactStr(name) end)
    if ok and type(v) == "number" then
        return v
    end
    ok, v = pcall(function() return qs:GetFact(CName.new(name)) end)
    if ok and type(v) == "number" then
        return v
    end
    return nil
end

function Story.snapshot()
    local qs = Game.GetQuestsSystem()
    local out = {}
    for _, f in ipairs(FACTS) do
        out[#out + 1] = { id = f.id, fact = f.fact, value = fact_value(qs, f.fact) }
    end
    return out
end

-- Sohbet baslamadan hemen once cagir (onOverlayOpen). Sadece degeri >= 1 olanlarin id listesini yazar.
function Story.write_brain()
    local ok, snap = pcall(Story.snapshot)
    if not ok then
        print("[Judy] Fact'ler okunamadi: " .. tostring(snap))
        return false
    end
    local done = {}
    for _, e in ipairs(snap) do
        if e.value ~= nil and e.value >= 1 then
            done[#done + 1] = '"' .. e.id .. '"'
        end
    end
    local payload = '{"done": [' .. table.concat(done, ", ") .. "]}"
    local file = io.open("judy_brain.json", "w")
    if not file then
        print("[Judy] Hata: judy_brain.json yazilamadi!")
        return false
    end
    file:write(payload)
    file:close()
    return true
end

-- TANI: hotkey ile cagir. Her fact'in gercek degerini CET konsoluna basar.
function Story.dump()
    local ok, snap = pcall(Story.snapshot)
    if not ok then
        print("[Judy] snapshot hatasi: " .. tostring(snap))
        return
    end
    print("[Judy] ---- Fact degerleri (yazilanlar) ----")
    for _, e in ipairs(snap) do
        print(string.format("[Judy] %-12s %-22s = %s", e.id, e.fact, tostring(e.value)))
    end
    print("[Judy] ---- Ek tani fact'leri (dosyaya yazilmaz) ----")
    local qs = Game.GetQuestsSystem()
    for _, name in ipairs(DIAG) do
        print(string.format("[Judy] %-26s = %s", name, tostring(fact_value(qs, name))))
    end
end

return Story
