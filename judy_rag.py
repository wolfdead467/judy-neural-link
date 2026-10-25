"""
Judy RAG yonlendirici: buyuk RAG dosyasini her istekte komple gondermek yerine
  - CEKIRDEK (kisilik + kurallar + cikti formati) her istekte gider
  - ANI PARCALARI (lore_memory) sadece V'nin mesajiyla ilgiliyse, en fazla MAX_CHUNKS tane eklenir
LLM cagrisi yok, tamamen yerel ve anlik.

RAG dosyasi yapisi (mevcut dosyani degistirmen gerekmez):
    <baslik / kurallar>
    # LORE AND MEMORY DATABASE (RAG CONTEXT)
    <aciklama>
    [ { "id":..., "keywords":[...], "content":..., "emotional_state":... }, ... ]
    # OUTPUT FORMAT RULE
    <cikti formati>

Hizli test (kendi RAG dosyanla):
    python judy_rag.py RAG "evelyn hakkinda ne hissediyorsun"
"""
import json
import re
import sys

MAX_CHUNKS = 2          # tek istekte en fazla kac ani parcasi eklensin
MIN_SCORE = 1.0         # bu skorun altindaki parca eklenmez (genel kelimeler zaten GENERIC_KEYWORDS ile elenir)
MAX_CHUNK_CHARS = 1400  # cok uzun parcayi kes (yaklasik 350 token)

LORE_MARK = "# LORE AND MEMORY DATABASE"
OUTPUT_MARK = "# OUTPUT FORMAT RULE"

# Her sohbette gecebilecek, tek basina parca tetiklememesi gereken genel kelimeler.
GENERIC_KEYWORDS = {
    "v", "bd", "help", "past", "memory", "system", "company", "night city", "plan", "boss",
    "power", "lies", "fight", "danger", "death", "doll", "relationship", "sacrifice",
    "promotion", "office", "security", "poor", "mocking", "justice", "guilt", "trauma",
    "relic", "child", "farewell",
}

# RAG'deki keywords'e ek: V'nin soyleyebilecegi ama listede olmayan kelimeler (Turkce dahil).
EXTRA_KEYWORDS = {
    "memory_evelyn_suicide": ["evie", "evelyn", "banyo", "kuvet", "intihar", "bathroom"],
    "memory_evelyn_heist_prep": ["evie", "evelyn", "soygun", "braindance", "heist"],
    "memory_evelyn_rescue_scavs": ["evie", "evelyn", "scav", "kurtar", "rescue"],
    "memory_maiko_breakup_office": ["maiko", "ayrilik", "ayrildin", "eski sevgili"],
    "memory_maiko_clothes_argument": ["maiko", "kiyafet"],
    "memory_maiko_clouds_betrayal": ["maiko", "ihanet", "hiromi"],
    "memory_maiko_jealousy_mox_anniversary": ["maiko", "kiskanc", "kiskanma"],
    "memory_clouds_maiko_start": ["clouds", "maiko"],
    "memory_childhood_origins": ["cocukluk", "ailen", "annen", "baban", "dedesi", "family", "granddad"],
    "memory_childhood_streets": ["cocukken", "hamburger", "hockey"],
    "memory_grandmother_ainara": ["babaanne", "nine", "grandma", "sinirli", "temper"],
    "memory_jenni_chapman_doll": ["jenni", "ilk ask", "first crush"],
    "memory_laguna_bend_eviction": ["laguna", "baraj", "reservoir"],
    "memory_firetruck_injustice": ["itfaiye", "polis", "hapis", "cops", "cop"],
    "memory_lizzies_bar_mox": ["mox", "lizzie", "susie"],
    "memory_clouds_takeover_plan": ["pizza", "roxanne", "seizure", "nobet"],
}


def _has(text: str, kw: str) -> bool:
    """Kelime siniri ile ara. Kisa kelimeler (<=3) tam eslesmeli; uzunlar ek alabilir (jail -> jailed)."""
    kw = kw.strip().lower()
    if not kw:
        return False
    tail = r"(?![a-z0-9])" if len(kw) <= 3 else r"[a-z]{0,3}(?![a-z0-9])"
    return re.search(r"(?<![a-z0-9])" + re.escape(kw) + tail, text) is not None


def _fold(s: str) -> str:
    """Turkce karakterleri sadelestir ki 'ihanet', 'kıskanç' gibi yazimlar tutsun."""
    return (s or "").lower().translate(str.maketrans("çğıöşü", "cgiosu")).replace("i̇", "i")


class JudyRag:
    def __init__(self, raw: str):
        raw = raw.replace("\r\n", "\n")
        i_lore = raw.find(LORE_MARK)
        i_out = raw.find(OUTPUT_MARK)
        self.chunks = []
        if i_lore < 0 or i_out < 0 or i_out < i_lore:
            self.head, self.tail = raw.strip(), ""
            print("[judy-rag] UYARI: RAG basliklari bulunamadi, hepsi cekirdek sayildi.")
            return

        self.head = raw[:i_lore].rstrip()
        self.tail = raw[i_out:].strip()
        lore = raw[i_lore:i_out]
        a, b = lore.find("["), lore.rfind("]")
        if a >= 0 and b > a:
            try:
                self.chunks = json.loads(lore[a : b + 1])
            except ValueError as e:
                print(f"[judy-rag] UYARI: JSON dizisi okunamadi, parcasiz devam: {e}")

    # ------------------------------------------------------------------ #
    def add_chunks(self, chunks) -> int:
        """Dal parcalari gibi ek parcalari ekler. Alanlar: id, content (zorunlu); keywords, emotional_state,
        requires, requires_not, enabled, anchor, boost (istege bagli). Gecersiz girdiler atlanir."""
        good = [c for c in (chunks or []) if isinstance(c, dict) and c.get("id") and c.get("content")]
        self.chunks.extend(good)
        return len(good)

    @staticmethod
    def _eligible(chunk: dict, state) -> bool:
        """enabled=false ise asla. requires'in HEPSI state'te, requires_not'in HICBIRI state'te olmali."""
        if chunk.get("enabled") is False:
            return False
        state = state or set()
        if any(r not in state for r in (chunk.get("requires") or [])):
            return False
        if any(n in state for n in (chunk.get("requires_not") or [])):
            return False
        return True

    def _score(self, chunk: dict, query: str, recent: str) -> float:
        # anchor: konu kelimelerinden en az biri V'nin mesajinda ya da son konusmada gecmeli
        # ("is she dead" gibi devam sorularinda konu son konusmadan gelir).
        anchors = chunk.get("anchor") or []
        if anchors and not any(_has(query, _fold(a)) or _has(recent, _fold(a)) for a in anchors):
            return 0.0
        kws = list(chunk.get("keywords", [])) + EXTRA_KEYWORDS.get(chunk.get("id", ""), [])
        s = 0.0
        seen = set()
        for kw in kws:
            k = _fold(kw)
            if k in seen:
                continue
            seen.add(k)
            if k in GENERIC_KEYWORDS:
                continue
            strong = " " in k or len(k) > 5
            if _has(query, k):
                s += 2.0 if strong else 1.0
            elif recent and _has(recent, k):
                s += 0.5 if strong else 0.25  # son konusmada gecen konu hala sicak
        if s >= MIN_SCORE:
            s += float(chunk.get("boost", 0) or 0)  # dal parcalari genel anilarin onune gecsin
        return s

    def select(self, user_text: str, recent_text: str = "", state=None) -> list[dict]:
        q, r = _fold(user_text), _fold(recent_text)
        scored = [(self._score(c, q, r), c) for c in self.chunks if self._eligible(c, state)]
        scored = [(s, c) for s, c in scored if s >= MIN_SCORE]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:MAX_CHUNKS]]

    def build_prompt(self, user_text: str, recent_text: str = "", extra: str = "", state=None) -> str:
        """extra: cikti formati kuralindan ONCE eklenecek ek blok (ornegin paylasilan gecmis).
        state: oyunda gerceklesmis olay id'leri kumesi (dal parcalarinin requires sartlari icin)."""
        picked = self.select(user_text, recent_text, state)
        parts = [self.head]
        if picked:
            parts.append(
                "# MEMORIES RELEVANT TO WHAT V JUST SAID\n"
                "These are your own memories. Use them naturally, in your own words. Do not recite them."
            )
            for c in picked:
                content = c.get("content", "")
                if len(content) > MAX_CHUNK_CHARS:
                    content = content[:MAX_CHUNK_CHARS].rsplit(" ", 1)[0] + "..."
                parts.append(f"- {content}\n  (Feeling: {c.get('emotional_state', '')})")
        else:
            parts.append("# MEMORIES\nNo specific memory is triggered. Talk from your normal life and feelings.")
        if extra:
            parts.append(extra)
        parts.append(self.tail)
        return "\n\n".join(p for p in parts if p)

    def picked_ids(self, user_text: str, recent_text: str = "", state=None) -> list[str]:
        return [c.get("id", "?") for c in self.select(user_text, recent_text, state)]


def approx_tokens(s: str) -> int:
    return int(len(s) / 3.6)  # kaba tahmin


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit('Kullanim: python judy_rag.py RAG "V mesaji" ["onceki konusma"]')
    with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
        raw = f.read()
    rag = JudyRag(raw)
    msg = sys.argv[2]
    rec = sys.argv[3] if len(sys.argv) > 3 else ""
    prompt = rag.build_prompt(msg, rec)
    print(f"parca sayisi: {len(rag.chunks)} | secilen: {rag.picked_ids(msg, rec)}")
    print(f"eski prompt ~{approx_tokens(raw)} token -> yeni ~{approx_tokens(prompt)} token")
