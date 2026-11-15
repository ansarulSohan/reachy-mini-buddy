"""The five storytelling languages: voices, speaking rates, phrases and an offline story.

To change a voice, edit VOICES. List all neural voices with:
    .venv/bin/edge-tts --list-voices
"""

from __future__ import annotations

import re

LANGS = ("en", "no", "bn", "hi", "ur")

NAMES = {"en": "English", "no": "Norwegian (Bokmål)", "bn": "Bengali (Bangla)",
         "hi": "Hindi", "ur": "Urdu"}

# Neural voices (edge-tts, needs internet). Gentle female voices; en-US-AnaNeural is a child voice.
VOICES = {
    "en": "en-US-AnaNeural",
    "no": "nb-NO-PernilleNeural",
    "bn": "bn-BD-NabanitaNeural",   # Indian Bengali: bn-IN-TanishaaNeural
    "hi": "hi-IN-SwaraNeural",
    "ur": "ur-PK-UzmaNeural",       # Indian Urdu: ur-IN-GulNeural
}

# Offline fallback: macOS `say` voices (there is no Urdu voice on macOS).
SAY_VOICES = {"en": "Samantha", "no": "Nora", "bn": "Piya", "hi": "Lekha", "ur": None}

# Slow, child-friendly speaking rate (edge-tts rate -10%), in words per minute.
# Claude uses it to turn "minutes" into a word count.
WORDS_PER_MINUTE = {"en": 130, "no": 115, "bn": 100, "hi": 115, "ur": 115}

PHRASES: dict[str, dict[str, str]] = {
    "greet": {
        "en": "Hi friend! I'm Reachy, the story robot. I can tell stories in English, Norsk, Bangla, Hindi and Urdu.",
        "no": "Hei, venn! Jeg er Reachy, fortellerroboten. Jeg kan fortelle historier på engelsk, norsk, bengali, hindi og urdu.",
        "bn": "হ্যালো বন্ধু! আমি রিচি, গল্প বলা রোবট। আমি ইংরেজি, নরওয়েজিয়ান, বাংলা, হিন্দি আর উর্দুতে গল্প বলতে পারি।",
        "hi": "नमस्ते दोस्त! मैं रीची हूँ, कहानी सुनाने वाला रोबोट। मैं अंग्रेज़ी, नॉर्वेजियन, बांग्ला, हिंदी और उर्दू में कहानियाँ सुना सकता हूँ।",
        "ur": "السلام علیکم دوست! میں ریچی ہوں، کہانی سنانے والا روبوٹ۔ میں انگریزی، نارویجن، بنگالی، ہندی اور اردو میں کہانیاں سنا سکتا ہوں۔",
    },
    "ask": {
        "en": "What story would you like? Tell me what it should be about, and how long it should be.",
        "no": "Hvilken historie vil du høre? Si hva den skal handle om, og hvor lang den skal være.",
        "bn": "তুমি কোন গল্প শুনতে চাও? গল্পটা কী নিয়ে হবে আর কত লম্বা হবে, আমাকে বলো।",
        "hi": "तुम कौन सी कहानी सुनना चाहते हो? मुझे बताओ कहानी किस बारे में हो, और कितनी लंबी हो।",
        "ur": "تم کون سی کہانی سننا چاہتے ہو؟ مجھے بتاؤ کہانی کس بارے میں ہو، اور کتنی لمبی ہو۔",
    },
    "didnt_hear": {
        "en": "Oops, I didn't hear you. Can you say it again, a little louder?",
        "no": "Oi, jeg hørte deg ikke. Kan du si det igjen, litt høyere?",
        "bn": "উফ, আমি শুনতে পাইনি। আরেকবার একটু জোরে বলবে?",
        "hi": "अरे, मैंने सुना नहीं। क्या तुम थोड़ा ज़ोर से फिर से बोलोगे?",
        "ur": "ارے، میں نے سنا نہیں۔ کیا تم تھوڑا زور سے دوبارہ بولو گے؟",
    },
    "i_pick": {
        "en": "That's okay! I'll pick a story for you.",
        "no": "Det går bra! Jeg velger en historie til deg.",
        "bn": "ঠিক আছে! আমি তোমার জন্য একটা গল্প বেছে নিচ্ছি।",
        "hi": "कोई बात नहीं! मैं तुम्हारे लिए एक कहानी चुनता हूँ।",
        "ur": "کوئی بات نہیں! میں تمہارے لیے ایک کہانی چنتا ہوں۔",
    },
    "thinking": {
        "en": "Ooh, good idea! Let me think of a story...",
        "no": "Å, så gøy! La meg tenke på en historie...",
        "bn": "বাহ, দারুণ! দাঁড়াও, একটা গল্প ভাবি...",
        "hi": "अरे वाह! रुको, मैं एक कहानी सोचता हूँ...",
        "ur": "واہ، زبردست! رکو، میں ایک کہانی سوچتا ہوں...",
    },
    "title": {
        "en": "Today's story is called: {title}.",
        "no": "Dagens historie heter: {title}.",
        "bn": "আজকের গল্পের নাম: {title}।",
        "hi": "आज की कहानी का नाम है: {title}।",
        "ur": "آج کی کہانی کا نام ہے: {title}۔",
    },
    "where": {
        "en": "Hey, where did you go? I'll wait for you.",
        "no": "Hei, hvor ble du av? Jeg venter på deg.",
        "bn": "আরে, তুমি কোথায় গেলে? আমি তোমার জন্য অপেক্ষা করছি।",
        "hi": "अरे, तुम कहाँ चले गए? मैं तुम्हारा इंतज़ार कर रहा हूँ।",
        "ur": "ارے، تم کہاں چلے گئے؟ میں تمہارا انتظار کر رہا ہوں۔",
    },
    "welcome_back": {
        "en": "There you are! Let's keep going.",
        "no": "Der er du! Da fortsetter vi.",
        "bn": "এই তো তুমি! চলো, আবার শুরু করি।",
        "hi": "अरे, तुम आ गए! चलो, आगे सुनते हैं।",
        "ur": "ارے، تم آ گئے! چلو، آگے سنتے ہیں۔",
    },
    "time_up": {
        "en": "And that is where our story stops for today. The end!",
        "no": "Og der stopper historien vår for i dag. Snipp, snapp, snute!",
        "bn": "আজকের মতো গল্প এখানেই থামল। গল্পটি শেষ!",
        "hi": "आज के लिए हमारी कहानी यहीं रुकती है। कहानी ख़त्म!",
        "ur": "آج کے لیے ہماری کہانی یہیں رکتی ہے۔ کہانی ختم!",
    },
    "another": {
        "en": "Did you like it? Do you want another story?",
        "no": "Likte du den? Vil du høre en historie til?",
        "bn": "তোমার ভালো লেগেছে? আরেকটা গল্প শুনবে?",
        "hi": "तुम्हें कैसी लगी? एक और कहानी सुनोगे?",
        "ur": "تمہیں کیسی لگی؟ ایک اور کہانی سنو گے؟",
    },
    "bye": {
        "en": "Okay! Bye bye, see you next time!",
        "no": "Greit! Ha det bra, vi sees!",
        "bn": "আচ্ছা! টাটা, আবার দেখা হবে!",
        "hi": "ठीक है! टाटा, फिर मिलेंगे!",
        "ur": "ٹھیک ہے! خدا حافظ، پھر ملیں گے!",
    },
}


# Mood reader: Reachy asks about the mood it sees.
PHRASES.update({
    "mood_happy": {
        "en": "You look so happy! What made you smile?",
        "no": "Du ser så glad ut! Hva fikk deg til å smile?",
        "bn": "তোমাকে খুব খুশি লাগছে! কী দেখে হাসলে?",
        "hi": "तुम बहुत खुश लग रहे हो! किस बात पर मुस्कुरा रहे हो?",
        "ur": "تم بہت خوش لگ رہے ہو! کس بات پر مسکرا رہے ہو؟",
    },
    "mood_sad": {
        "en": "Oh, you look a little sad. What happened?",
        "no": "Å, du ser litt lei deg ut. Hva har skjedd?",
        "bn": "ওহ, তোমাকে একটু মন খারাপ লাগছে। কী হয়েছে?",
        "hi": "अरे, तुम थोड़े उदास लग रहे हो। क्या हुआ?",
        "ur": "ارے، تم تھوڑے اداس لگ رہے ہو۔ کیا ہوا؟",
    },
    "mood_surprised": {
        "en": "Whoa! You look surprised! What did you see?",
        "no": "Oi! Du ser overrasket ut! Hva så du?",
        "bn": "ওমা! তোমাকে অবাক লাগছে! কী দেখলে?",
        "hi": "अरे वाह! तुम हैरान लग रहे हो! क्या देखा?",
        "ur": "ارے واہ! تم حیران لگ رہے ہو! کیا دیکھا؟",
    },
    "mood_angry": {
        "en": "Uh oh, you look angry. What's wrong?",
        "no": "Å nei, du ser sint ut. Hva er galt?",
        "bn": "ওহ, তোমাকে রাগী লাগছে। কী হয়েছে বলো তো?",
        "hi": "ओह, तुम गुस्से में लग रहे हो। क्या बात है?",
        "ur": "اوہ، تم غصے میں لگ رہے ہو۔ کیا بات ہے؟",
    },
    "mood_confused": {
        "en": "Hmm, you look confused. Is something tricky?",
        "no": "Hmm, du ser forvirret ut. Er noe vanskelig?",
        "bn": "হুম, তোমাকে একটু দ্বিধায় লাগছে। কিছু কি কঠিন লাগছে?",
        "hi": "हम्म, तुम उलझन में लग रहे हो। कुछ मुश्किल लग रहा है?",
        "ur": "ہمم، تم الجھن میں لگ رہے ہو۔ کچھ مشکل لگ رہا ہے؟",
    },
    "awake": {
        "en": "Hi! I'm awake! Show me some fingers, or ask me for a story.",
        "no": "Hei! Jeg er våken! Vis meg noen fingre, eller be meg om en historie.",
        "bn": "হাই! আমি জেগে উঠেছি! আমাকে আঙুল দেখাও, অথবা একটা গল্প শুনতে চাও বলো।",
        "hi": "हाय! मैं जाग गया! मुझे उँगलियाँ दिखाओ, या मुझसे कहानी सुनाने को कहो।",
        "ur": "ہائے! میں جاگ گیا! مجھے انگلیاں دکھاؤ، یا مجھ سے کہانی سنانے کو کہو۔",
    },
    "hi_again": {
        "en": "Hi again! I'm right here.",
        "no": "Hei igjen! Jeg er her.",
        "bn": "আবার হাই! আমি এখানেই আছি।",
        "hi": "फिर से हाय! मैं यहीं हूँ।",
        "ur": "پھر سے ہائے! میں یہیں ہوں۔",
    },
    "help": {
        "en": "I can count your fingers, or tell you a story. Just ask!",
        "no": "Jeg kan telle fingrene dine, eller fortelle en historie. Bare spør!",
        "bn": "আমি তোমার আঙুল গুনতে পারি, অথবা গল্প বলতে পারি। শুধু বলো!",
        "hi": "मैं तुम्हारी उँगलियाँ गिन सकता हूँ, या कहानी सुना सकता हूँ। बस कहो!",
        "ur": "میں تمہاری انگلیاں گن سکتا ہوں، یا کہانی سنا سکتا ہوں۔ بس کہو!",
    },
    "thanks_feelings": {
        "en": "Thank you for telling me. Do you want to hear a story?",
        "no": "Takk for at du fortalte meg det. Vil du høre en historie?",
        "bn": "আমাকে বলার জন্য ধন্যবাদ। একটা গল্প শুনবে?",
        "hi": "मुझे बताने के लिए शुक्रिया। क्या तुम एक कहानी सुनना चाहोगे?",
        "ur": "مجھے بتانے کا شکریہ۔ کیا تم ایک کہانی سننا چاہو گے؟",
    },
    "okay": {
        "en": "Okay!",
        "no": "Greit!",
        "bn": "আচ্ছা!",
        "hi": "ठीक है!",
        "ur": "ٹھیک ہے!",
    },
    "good_night": {
        "en": "Good night! Say hi Reachy when you want to play again.",
        "no": "God natt! Si hei Reachy når du vil leke igjen.",
        "bn": "শুভ রাত্রি! আবার খেলতে চাইলে বলো, হাই রিচি।",
        "hi": "शुभ रात्रि! फिर से खेलना हो तो कहना, हाय रीची।",
        "ur": "شب بخیر! پھر سے کھیلنا ہو تو کہنا، ہائے ریچی۔",
    },
    "nobody": {
        "en": "Nobody is here. I'll take a little nap.",
        "no": "Ingen er her. Jeg tar meg en liten lur.",
        "bn": "কেউ নেই। আমি একটু ঘুমিয়ে নিই।",
        "hi": "यहाँ कोई नहीं है। मैं थोड़ी देर सो जाता हूँ।",
        "ur": "یہاں کوئی نہیں ہے۔ میں تھوڑی دیر سو جاتا ہوں۔",
    },
    # Finger counter
    "fingers_1": {
        "en": "I see 1 finger!",
        "no": "Jeg ser 1 finger!",
        "bn": "আমি ১টা আঙুল দেখছি!",
        "hi": "मुझे 1 उँगली दिख रही है!",
        "ur": "مجھے 1 انگلی نظر آ رہی ہے!",
    },
    "fingers_n": {
        "en": "I see {n} fingers!",
        "no": "Jeg ser {n} fingre!",
        "bn": "আমি {n}টা আঙুল দেখছি!",
        "hi": "मुझे {n} उँगलियाँ दिख रही हैं!",
        "ur": "مجھے {n} انگلیاں نظر آ رہی ہیں!",
    },
    "fingers_0": {
        "en": "Show me your fingers!",
        "no": "Vis meg fingrene dine!",
        "bn": "আমাকে তোমার আঙুলগুলো দেখাও!",
        "hi": "मुझे अपनी उँगलियाँ दिखाओ!",
        "ur": "مجھے اپنی انگلیاں دکھاؤ!",
    },
})

BENGALI_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def fingers_phrase(n: int, lang: str) -> str:
    if n <= 0:
        return phrase("fingers_0", lang)
    if n == 1:
        return phrase("fingers_1", lang)
    num = str(n).translate(BENGALI_DIGITS) if lang == "bn" else str(n)
    return phrase("fingers_n", lang, n=num)


# Voice commands, in all five languages (and romanised).
def _rx(pat: str) -> "re.Pattern[str]":
    return re.compile(pat, re.IGNORECASE)


INTENTS = {
    # Whisper sometimes writes Bengali speech in Devanagari (गौल्पो = golpo), so cross-script spellings too.
    "story": _rx(r"\bstor(y|ies)\b|\btale\b|historie|eventyr|fortell|গল্প|গল্পো|কাহিনী|কাহানি|কাহিনি|"
                 r"कहानी|कहानि|किस्सा|गल्प|गौल्पो|गोल्पो|गोलपो|गल्पो|کہانی|قصہ|گلپ|kahani|kahaani|golpo|galpo|golp"),
    "sleep": _rx(r"good ?night|go to sleep|\bsleep\b|\bbye\b|goodbye|god natt|\bsov\b|ha det|শুভ রাত্রি|ঘুমাও|ঘুমিয়ে|টাটা|বাই|शुभ रात्रि|सो जाओ|टाटा|बाय|شب بخیر|سو جاؤ|خدا حافظ|بائے"),
    "fingers": _rx(r"finger|fingre|how many|আঙুল|उँगली|उंगली|انگلی"),
    "wake": _rx(r"\b(hi|hello|hey|hei|hallo|hiya|yo)\b|wake|våkn|god morgen|হাই|হ্যালো|ওঠো|জাগো|নমস্কার|সালাম|नमस्ते|हाय|हैलो|हेलो|उठो|जागो|سلام|ہائے|ہیلو|اٹھو|جاگو|salam|namaste"),
    "yes": _rx(r"\b(yes|yeah|yep|ok|okay|sure|please|ja|gjerne|haan|ha|ji)\b|হ্যাঁ|হুম|ঠিক আছে|আচ্ছা|हाँ|हां|ठीक|ज़रूर|ہاں|جی|ٹھیک|ضرور"),
    "name": _rx(r"reachy|reach|richie|ritchie|ricky|rishi|রিচি|রিচী|রীচি|रीची|रिची|रिच्ची|ریچی|ریچ"),
}


def intent_of(text: str) -> str | None:
    """The first matching voice command: story, sleep, fingers, wake, yes (or None)."""
    for name in ("story", "sleep", "fingers", "wake", "yes"):
        if INTENTS[name].search(text or ""):
            if name == "sleep" and INTENTS["story"].search(text):
                return "story"  # "a bedtime story about sleepy bears"
            return name
    return None


def phrase(key: str, lang: str, **kw) -> str:
    text = PHRASES[key].get(lang) or PHRASES[key]["en"]
    return text.format(**kw) if kw else text


# A short "no / bye / stop" answer, in any of the five languages (or romanised).
_NO = re.compile(
    r"\b(no|nope|nah|bye|stop|enough|not now|nei|ha det|stopp|ferdig|nahi|nahin|bas|tata)\b"
    r"|না|নাহ|টাটা|বাই|থাক|নো|नहीं|नही|बस|टाटा|बाय|नो|نہیں|نہی|بس|خدا حافظ|بائے|نو",
    re.IGNORECASE)


def is_no(text: str) -> bool:
    """True for a short refusal such as "no", "nei", "না", "नहीं", "نہیں"."""
    return len(text.split()) <= 3 and bool(_NO.search(text))


# Language names a child might say ("in Urdu", "på norsk", "বাংলায়", "हिंदी में", "اردو میں").
_NAMED = {
    "ur": r"urdu|اردو|उर्दू|উর্দু",
    "hi": r"hindi|हिंदी|हिन्दी|ہندی|হিন্দি",
    "bn": r"bangla|bengali|বাংলা|बांग्ला|बंगाली|بنگالی",
    "no": r"norsk|norwegian|norske|নরওয়েজিয়ান|नॉर्वेजियन|نارویجن",
    "en": r"english|engelsk|ইংরেজি|अंग्रेज़ी|अंग्रेजी|انگریزی",
}


def lang_named_in(text: str) -> str | None:
    """The story language the child asked for by name, if any."""
    for code, pat in _NAMED.items():
        if re.search(pat, text or "", re.IGNORECASE):
            return code
    return None


# Offline story (used when Claude can't be reached). Same tagged format Claude writes.
OFFLINE_STORY = {
    "en": """TITLE: Pip and the Little Lost Star
[calm] Once upon a time, on a quiet green hill, there lived a little rabbit named Pip.
[curious] One night, Pip saw something shiny in the grass. Blink, blink, blink! What could it be?
[surprised] It was a tiny star! It had fallen all the way down from the sky.
[sad] "I want to go home," said the little star softly. "My family is up there, and I am so far away."
[happy] "Don't worry," said Pip. "I will help you!"
[excited] Pip hopped to his friend Owl. Owl flapped her big wings. Flap, flap, flap! But she could not fly high enough.
[curious] Then Pip had an idea. He called all the fireflies on the hill. "Can you help our new friend?"
[happy] The fireflies glowed bright and lifted the little star up, up, up, into the dark blue sky.
[calm] The star twinkled and found its family. "Thank you, Pip!" it sparkled.
[sleepy] And every night after that, the little star shined just for Pip, while he fell asleep, warm and happy. The end.""",
    "no": """TITLE: Pip og den lille stjernen
[calm] Det var en gang, på en stille, grønn bakke, en liten kanin som het Pip.
[curious] En kveld så Pip noe som glitret i gresset. Blink, blink, blink! Hva kunne det være?
[surprised] Det var en bitte liten stjerne! Den hadde falt helt ned fra himmelen.
[sad] «Jeg vil hjem,» sa den lille stjernen stille. «Familien min er der oppe, og jeg er så langt borte.»
[happy] «Ikke vær redd,» sa Pip. «Jeg skal hjelpe deg!»
[excited] Pip hoppet bort til vennen sin, Ugla. Ugla flakset med de store vingene sine. Flaks, flaks, flaks! Men hun klarte ikke å fly høyt nok.
[curious] Da fikk Pip en idé. Han ropte på alle ildfluene på bakken. «Kan dere hjelpe vår nye venn?»
[happy] Ildfluene lyste sterkt og løftet den lille stjernen opp, opp, opp, inn i den mørkeblå himmelen.
[calm] Stjernen blinket og fant familien sin. «Tusen takk, Pip!» glitret den.
[sleepy] Og hver natt etter det lyste den lille stjernen bare for Pip, mens han sovnet, varm og glad. Snipp, snapp, snute, så var eventyret ute.""",
    "bn": """TITLE: পিপ আর ছোট্ট তারা
[calm] অনেক দিন আগে, এক শান্ত সবুজ পাহাড়ের উপর, পিপ নামে একটি ছোট্ট খরগোশ থাকত।
[curious] এক রাতে পিপ দেখল, ঘাসের মধ্যে কী যেন চকচক করছে। ঝিকমিক, ঝিকমিক, ঝিকমিক! ওটা কী হতে পারে?
[surprised] ওটা ছিল একটা ছোট্ট তারা! আকাশ থেকে একদম নিচে পড়ে গিয়েছিল।
[sad] "আমি বাড়ি যেতে চাই," ছোট্ট তারাটা আস্তে আস্তে বলল। "আমার পরিবার ওই উপরে, আর আমি কত দূরে!"
[happy] "ভয় পেও না," পিপ বলল। "আমি তোমাকে সাহায্য করব!"
[excited] পিপ লাফিয়ে লাফিয়ে তার বন্ধু পেঁচার কাছে গেল। পেঁচা তার বড় বড় ডানা ঝাপটাল। ঝপ, ঝপ, ঝপ! কিন্তু সে অত উঁচুতে উড়তে পারল না।
[curious] তখন পিপের মাথায় একটা বুদ্ধি এল। সে পাহাড়ের সব জোনাকিকে ডাকল। "তোমরা কি আমাদের নতুন বন্ধুকে সাহায্য করবে?"
[happy] জোনাকিরা উজ্জ্বল আলো জ্বেলে ছোট্ট তারাটাকে তুলে নিল, উপরে, উপরে, আরও উপরে, গাঢ় নীল আকাশে।
[calm] তারাটা ঝিকমিক করে তার পরিবারকে খুঁজে পেল। "ধন্যবাদ, পিপ!" সে ঝলমল করে বলল।
[sleepy] আর তারপর থেকে প্রতি রাতে ছোট্ট তারাটা শুধু পিপের জন্য জ্বলত, আর পিপ আরামে, খুশি মনে ঘুমিয়ে পড়ত। গল্পটি এখানেই শেষ।""",
    "hi": """TITLE: पिप और छोटा तारा
[calm] बहुत समय पहले, एक शांत हरी-भरी पहाड़ी पर, पिप नाम का एक छोटा खरगोश रहता था।
[curious] एक रात पिप ने घास में कुछ चमकता हुआ देखा। टिम-टिम, टिम-टिम, टिम-टिम! वह क्या हो सकता था?
[surprised] वह एक नन्हा सा तारा था! वह आसमान से नीचे गिर गया था।
[sad] "मुझे घर जाना है," छोटे तारे ने धीरे से कहा। "मेरा परिवार ऊपर है, और मैं बहुत दूर हूँ।"
[happy] "चिंता मत करो," पिप ने कहा। "मैं तुम्हारी मदद करूँगा!"
[excited] पिप उछलता-कूदता अपने दोस्त उल्लू के पास गया। उल्लू ने अपने बड़े-बड़े पंख फड़फड़ाए। फड़-फड़-फड़! पर वह इतना ऊँचा नहीं उड़ पाया।
[curious] तब पिप को एक तरकीब सूझी। उसने पहाड़ी के सारे जुगनुओं को बुलाया। "क्या तुम हमारे नए दोस्त की मदद करोगे?"
[happy] जुगनुओं ने तेज़ रोशनी जलाई और छोटे तारे को ऊपर, ऊपर, और ऊपर, गहरे नीले आसमान तक पहुँचा दिया।
[calm] तारा टिमटिमाया और उसे अपना परिवार मिल गया। "धन्यवाद, पिप!" वह चमकते हुए बोला।
[sleepy] और उस रात के बाद, हर रात वह छोटा तारा सिर्फ़ पिप के लिए चमकता, और पिप खुशी-खुशी आराम से सो जाता। कहानी ख़त्म।""",
    "ur": """TITLE: پِپ اور ننھا تارا
[calm] بہت پہلے کی بات ہے، ایک پُرسکون ہری بھری پہاڑی پر پِپ نام کا ایک چھوٹا سا خرگوش رہتا تھا۔
[curious] ایک رات پِپ نے گھاس میں کوئی چمکتی ہوئی چیز دیکھی۔ ٹِم ٹِم، ٹِم ٹِم، ٹِم ٹِم! وہ کیا ہو سکتی تھی؟
[surprised] وہ ایک ننھا سا تارا تھا! وہ آسمان سے نیچے گر گیا تھا۔
[sad] "مجھے گھر جانا ہے،" ننھے تارے نے آہستہ سے کہا۔ "میرا خاندان اوپر ہے، اور میں بہت دور ہوں۔"
[happy] "فکر مت کرو،" پِپ نے کہا۔ "میں تمہاری مدد کروں گا!"
[excited] پِپ اچھلتا کودتا اپنے دوست اُلّو کے پاس گیا۔ اُلّو نے اپنے بڑے بڑے پر پھڑپھڑائے۔ پھڑ، پھڑ، پھڑ! لیکن وہ اتنا اونچا نہیں اُڑ سکا۔
[curious] تب پِپ کو ایک ترکیب سوجھی۔ اس نے پہاڑی کے سارے جگنوؤں کو بلایا۔ "کیا تم ہمارے نئے دوست کی مدد کرو گے؟"
[happy] جگنوؤں نے اپنی تیز روشنی جلائی اور ننھے تارے کو اوپر، اوپر، اور اوپر، گہرے نیلے آسمان تک پہنچا دیا۔
[calm] تارا جگمگایا اور اسے اپنا خاندان مل گیا۔ "شکریہ، پِپ!" اس نے چمکتے ہوئے کہا۔
[sleepy] اور اس کے بعد ہر رات وہ ننھا تارا صرف پِپ کے لیے چمکتا، اور پِپ خوشی خوشی آرام سے سو جاتا۔ کہانی ختم ہوئی۔""",
}
