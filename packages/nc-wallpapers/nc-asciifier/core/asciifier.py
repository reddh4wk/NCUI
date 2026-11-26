#!/usr/bin/env python3
"""PNG-only ASCII art renderer and disk cache for NC-UI desktop wallpapers."""
# ponytail: png-only library for wallpaper pipeline; no CLI/txt/html exports

import hashlib
import json
import os
import shutil
import subprocess
import threading

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
    _HAS_PIL = True
except ImportError:
    Image = ImageDraw = ImageEnhance = ImageFont = ImageOps = None
    _HAS_PIL = False

REFERENCE_HEIGHT = 2880.0
BASE_TARGET_ROWS = 120.0
BASE_CELL_HEIGHT = REFERENCE_HEIGHT / BASE_TARGET_ROWS
FONT_TO_HEIGHT_RATIO = 20.0 / 24.0
_RENDER_VERSION = 2
_BAYER_2X2 = ((0.125, 0.625), (0.875, 0.375))

ALL_KATAKANA = (
    " ・ーヽヾゝゞ〃仝々〆〇"
    "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾ"
    "タダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポ"
    "マミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶヷヸヹヺ"
    "ㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ"
)

COMMON_2000_KANJIS = (
    " 一乙九七二人入八力十下三千上口土夕大女子小山川五天中六円手文日月木水火犬王正出本右四左玉生田白目石立百年休先名字早気竹糸耳虫村男町花見貝赤足車学林空金雨青草音"
    "校森刀万丸才工弓内午少元今公切分友父方北引心戸毛止比牛氏欠风不予井云互五什仁今介仏仕他付仙代令以仮仰仲件任企伎伏伐休会伝伯伴伸伺似伽佃但位低住佐佑体何余作使例侍供依価侮侯侵侶便係促俊俗保信修俳俵俸俺倉個倍倒候借倣値倦倫倭倶倹偉偏停健偲側偵偶偷偽傍傑傘備催傭傲債傷傾働像僑僕僚僧僮僥僵價僻儀億儒償優"
    "元兄充兆先光克免兎児党兜入全八公六共兵具典兼内円冊再冒冗写冠冥冬氷冷凄准凋凌凍凝凡処凧凪夙鳳凱凶凸凹出函刀刃分切刈刊刑列初判別利到制刷券刹刺刻剃則削前剖剛剣剤副剰割創劇劈劔力功加劣助努励労効劾勃勅勇勉動勘務勝労募勢勤勧勲励勺勿匁包匆匈匕化北匙匠匡匣匪匹区医匿十千升午半卑卒卓協南単博占卦印危即却卵卸厄厘厚原厨去参又叉及友双反収叔取受叙叛口古句叩只叫召叭叮可台叱史右叶号司叹叻吁各合吉吊同名后吏吐向君吝吞吟吠否吧吩含聴吸吹吻吼吾呀呂呈呉告呎呢周呪味呵呶呻呼命咀咄咆和咎咏咐咒咔咕咖咙咧咨咪咫咬咯咱咳咸咽哀品哂哄哆哇哈哉响哎哏哐哑唼"
    "四回因団困囲図固国囿圏園土圧在圭地坂均坊坍坎坏坐坑坡坤坦坪垂垃型垢垣埃埋城埣域埠堉執培基埼堀堂堅堆堕堤堪報場堵堺塀塁塊塑塔塗塚塞塩填塵塾境墓増墜墨墩堕墳墾壁壇壊壌士壬壮声壱売壺変夏夕外多夜夢大天太夫央失奇奈奉奏契奔套奥奨奪奮女奴好如妃妄妊妍妓妖妙妥妨妬妹妻妾姉始姐姑姓委妊姚姜姦姨姪姫姻姿威娃娉娑娘娚娜娟娠娥娩娯娶娼婀婆婉婚婢婦婪媒媛媚富嫁嫂嫉嫌嫡嫣嫦嫩嬉嬋嬌嬢嬪嬲嬾子字存孝季孤学孫孰孵学它宅宇守安完宏宗官宙定宜宝実客宣室宥宮宰害宴宵家容宿寂寄密富寒寓寛寝察寡寧審寮寸寺対寿封専射将尉尊尋導小少尖尚尤就尺尻尼尽尾尿局居屈届屋屍屎屏展属屠層履屯山屹岐岑岔岡岩岬岱岳岸峠峡峨峰島峻崇崎崖崩嵐嵩嵯嶺巌川州巡巣工左巧巨差己已巳巻币市布帆希帑帖帙帛帝帥師席帯帰帳常帽幅幌幕幡幣干平年幸幹幻幼幽幾庁広庄庇床序底店庚府度座庫庭庶康庸廃廉廊廐廓廖延廷建廻廿弁弄弊弋式弐弓弔引弗弘弛弟弥弦弧弩弱張強弼弾彊彦彩彪彫彬彭影彷役彼往征径待律後徐徒従得御復循微徳徴徹心必忌忍志忘忙応忠快念怒怖思怠急性怨怪怯恋恐恒恕恢恥恨恩恭息恰恵悉悌悍悔悟悠患悦悩悪悲悶悸悼情惑惚惜惟惨惰想惹愁愈愉意愚愛感慈態慌慎慕惨慚慟慢慣慧慨慮慰慶憂憎憐憤憧憩憲憶憾懇懐懲懸戌成我戒或戚戟戦戯戸戻房所扇扉手才打払托扮扱扶批承技抄把抑投抗折抜択抵抹押抽担拆拇拈拉拊抛拌拍拏拐拒拓抜拘拙招拝拠拡括拭拮拱拳拶拷拾拿持挂指按挑挙挟挨挫振挺挽挿捉捌捍捏捐捕捗捜捧捩据捲捷捺捻掀掃授掉掌排掘掛掟掠採探接控推掩措掬掲掴掻掾揀揃描提挿揚換握掲揮援揺損搬搭携搾摂摘摩摸摺撃撒撮播撫撚撞撤撮撰撲撹撻撼擁操擦擬擡擢擯擾"
    "支収改攻放政故敏救敗教敢散敬数整敵敷文斉斌斗料斜斤斥斧斬断斯新方於施旁旅旋族旗既日旦旧旨早旬旭旱時旺昂昆昇昉昊昌明昏易昔星映春昧昨昭是昼時晃晋晒晦晩普景晴晶智暁暇暉暑暖暗暦暫暮暴曇曖曜曲更書曹曽替最月有服朋朗望朝期木未末本札朱朴朵机朽杉李杏材村杓杖束条来杯東杵松板析枕林枚果枝枠枢枯架枷枸柄柏某柑染柔柘柚柱柳柴柵査柾柿栂栄栓校株根格栽桁桃案桑桜桟梅梗梨梯械梶棄棋棒棚棟森棺椀椅椋植椎椛検梺椿楊楓楕楚楠楢業楯極楼楽概榊榎樺模様横樹樽橋橘機橡楕橿檀檄櫛欄欠次欧欲欺款歌歓止正此武歩歪歯歳歴死殉殊残殖殴段殺殻殿母毎毒比毛氏民気水氷永氾汁求汎汐汕汗汚汝江池汰汲決汽沃沈沌沍没沖沙沚沛没沢沫沮河沸油治沼沿況泉泊泌法泡波泣泥注泪泰泳洋洗洛洞津洪洲洵活派流浄浅浜浦浩浪浮浴海浸消涌涙涯液涼淀淑淘淡淫深淳淵混添清渇済渉渋渓渚減渡渦温測港湖湧湯湾湿満源準溜溝溢溶溺滅滋滑滝滞滴漁漂漆漏演漠漢漫漬漸潔潘潜潟潤潮潰澄激濁濃濠濡済濯瀕瀑火灯灰灸灼災炉炊炎炭点為烈烏烙烘烹烽焉焚無焦然焼煉煌煎煙煤照煩煮煽熄熊熏熔熟熱燃燈燐燕燥燦燧燬燮爪爬争爵父爷爽爾片版牌牒牙牛牝牟牡牢牧物牲特牽犀犠犬犯状狂狙狛狎狐狗狙狩独狭狸狼猛猟猫献猶猿獄獅獣獲玄率玉王玖弄珍珠珪班現球理琉琢琥琲琳琴琵琶瑚瑛瑞瑠瑳瑶璃璋璞環璽瓜瓢瓦瓶甘甚生産甥用田由甲申男町画界畏畑畔留畜畝略番画異畳畴當畿疆疎疑疫疲疾病症痊痍痒痔痕痘痙痛痞痢痩痴瘉癇療癒癖癘癸発登白百皂的皆皇皈皮皿盃盆盈益盍盒蓋盗盤盛盟監盤目盲直相盾省眄眇眈眉看県真眠眛眷眸眺眼着睡督睦瞬瞭瞳矛矢知短矯石砂研砕砥砧砲破砺硅硝硫硬碁碑碓碕碗碧碩磁磐磨磯礁礎示礼社祀祁祇祈祉祐祖祝神祢祥票祭祷禁禄禅禍禎福秀私秋科秒秘租秤秦秩称移稀程税稔稗稚稜種稲稼稽稿穀穂穆積穏穫穴究空穿突窃窄窈窒窓窘窟窩窪窮窯窺竄立竜章竣童端競竹竺竿笂笄笑笙笛笞笠笥符第笳筆等筋筍筏筐筑筒答策筠筺筬筮筱算管箚箱箴箸節範篇築簡簀簇簔籍米粉粋粒粕粗粘粛粟粥粧精糊糎糖糞糧糸系糾紀約紅紆紊紋納紐純紗紘紙級紛素紡索紫紬累細紳紹紺終絃組経結絞絡絢給絨統絵絶絹継続綜緑緒線締編緩緬緯練緻縁縄縛縞縦縫縮績繁繊繋繍纂繹羅羊美羞群羨義羽翁翌習翠翻翼耀老考者耄耐耒耕耗耳耶恥耽聡聴職肉肌肝股肢肥肩肪肯育肺胃胆背胎胞胴胸能脂脅脆脈脊脚脱脳脹脾腋腎腐腕腫腰腸腹腺腿膏膚膜膝膨膳膿臆臓臣臨自臭至致臼興舌舎舗舞舟航般舶舵船艇艦良色艶艸芝芥芦芭芯花芳芸芹芽苅苑苔苗苛若苦苫英茂范茄茅茎茜茨茶茸草荊荏薦荒荘荷荻荼莅茣莫莱蓮蒔蒙蒜蒲蒸蒼蓄蓉蓋蓑蓬蔑蔓蔚蔡蔦蔬蔭蔵蔽蕃蕉蕊蕎蕗蕨蕩蕪薄薇薊薔薛薦薩薪薫薬薮藁藉藍蔵藝藤藩藻蘭虎虐虚虜虞虫虹蚊蚋蚌蚓蚕蚣蚤蚩蚪蚯蚰蚶蛄蛆蛇蛉蛋蛍蛎蛔蛙蛛蛞蛟蛤蠎蛭蛮蛸蛹蛾蜀蜂蜃蜆蜈蜉蜍蜊蜒蜓蜘蜜蜞蝋蝎蝴蝗蝨蝮蝙蝟蝠蝦蝨蠑螈融螟螢螫螳螺蟋螽蟀蟄蟆蟇蟋蟷蟒蟹蟻蟾蠅蠍蠇蠎蟹蠢血衆行術街衛衝衡衣表衰衲衷衽衾衿袈袋袖被袴裁裂装裏裕補裸製裾複合褚褊褐褒褓褝褥褪褫褶褸襁襟襲西要覆見規視覗覚覧親観角解触言訂訃計討訓訖託記訟訪設許訳訴訶診証詐詒詔評詞詠詢詣試詩詫詮詰話該詳誂詼誅誇誉誌認誓誕誘語誠誡誤説読誰課誹誼調諂談請諌諍諏諒論諜諡諭諮諱諳諸諺諾謀謁謂謄謎謙講謝謡謬謹謾証譎譏識譜警欄訳議譴護誉読変譲讃谷豆豊豚象豪豹貌貝貞負財貢貧貨販貪貫責貯貰貴買貸費貼貿賀賂賃賄資賊賑賓賛賜賞賠賢売賤賦質賭購贈贋贓贖赤赦赫走赴起超越趨足距跡路跳践踊踏踝踞踢踩踪踰踴踵蹂蹄蹇蹈蹉蹊蹋蹙蹟躍身躬躯車軌軍軒軟転軸軽較載輝輩輪輸轄轍辛辞辣辱農辺辻込辿迂迄迅迎近返迦迨迩迫迭述迷迸迹追退送逃逆透逐逓途通逝逞速造逢連逮週進逸逼遁遂遅遇遊運遍過道達違遜遠遡遣遥適遭遮遅遵遷選遺遼避還邁邂邃邑那邦邪邸郁郊郎郡部郭郵郷都酌配酎酒酔酢酪酬酵酷酸醜醸采釈里重野量金針釣鈍鈴鉄鉛鉢鉱銀銃銅銑銘銚銭鋒鋤舗鋭鋲鋳鋸鋼錆錐錘錠錦錨錫錬錯録鍋鍍鍔鍛鍬鍵鎌鎖鎮鏡鐘鑑長門閉開閑間関閣閥閲闇闘阜防阻阿陀附降限陛院陣除陥陪陰陳陵陶陸険陽隅隆隈隊階随隔隙際障隠隣隧隷隻隼雀雁雄雅集雇雌雑離難雨雪雫雰雲零雷電需震霊霜霞霧露青靖静非面革靱靴鞄鞍鞘鞠鞭音韻響頁頂頃項順須預頑頒頓頗領頡題額顎顔顕願顛類顧顕風颯颱颶飛食飢飯飲飴飼飽飾餅養餌餐餒餓餘餡館首香馨馬駄駅駆駐駒駕駿騎騒験騙驚骨骸髄高髪髭髟鬢闘鬧鬱鬼魁魂魅魔魚魯鮎鮑鮒鮓鮠鮭鮮鯉鯖鯛鯨鰐鰭鳥鳩鳳鳴鳶鴉鴎鴛鴫鴻鵜鵠鶏鶴鷲鷹鷺鹿麓麒麗麝麦麹麻麿黄黒黙黛黜点黝鼠鼻斉歯齢龍亀"
)

CHAR_SETS = {
    "standard": " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    "binary": " 01",
    "katakana": ALL_KATAKANA,
    "kanjis": COMMON_2000_KANJIS,
}

FULLWIDTH_SETS = {"kanjis", "katakana"}
COLOR_MODES = ("color", "grayscale", "matrix", "amber")
ASCII_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
def _ascii_cache_dir() -> str:
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip() or "~/.cache"
    return os.path.join(os.path.expanduser(xdg), "ncui", "asciified")


ASCII_CACHE_DIR = _ascii_cache_dir()

ASCIIFY_DEFAULTS = {
    "enabled": False,
    "mode": "color",
    "chars": "standard",
    "density": 1.0,
    "tint": 0.22,
    "scale": 1.0,
    "contrast": 1.0,
    "brightness": 1.0,
    "invert": False,
}

_CACHE_LOCK = threading.Lock()
_FONT_PATH_CACHE: dict[str, str] = {}
_FONT_CACHE: dict[tuple[str, int], object] = {}
_SORTED_CHARS_CACHE: dict[tuple[str, str], str] = {}


def is_pillow_available() -> bool:
    return _HAS_PIL


def _clamp_f(val, lo: float, hi: float, default: float) -> float:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return round(max(lo, min(hi, v)), 2)


def normalize_cfg(cfg: dict | None) -> dict:
    c = cfg if isinstance(cfg, dict) else {}
    mode = str(c.get("mode", "color") or "color").strip().lower()
    if mode not in COLOR_MODES:
        mode = "color"

    # Never strip custom chars: leading spaces represent dark/empty cells.
    raw_chars = c.get("chars", "standard")
    if raw_chars == "blocks":
        chars = "standard"
    elif isinstance(raw_chars, str) and raw_chars in CHAR_SETS:
        chars = raw_chars
    elif isinstance(raw_chars, str) and len(raw_chars) >= 2 and raw_chars.strip() != "":
        chars = raw_chars
    else:
        chars = "standard"

    return {
        "enabled": bool(c.get("enabled", False)),
        "mode": mode,
        "chars": chars,
        "density": _clamp_f(c.get("density", 1.0), 0.25, 2.5, 1.0),
        "tint": _clamp_f(c.get("tint", 0.22), 0.0, 1.0, 0.22),
        "scale": _clamp_f(c.get("scale", 1.0), 0.25, 1.5, 1.0),
        "contrast": _clamp_f(c.get("contrast", 1.0), 0.5, 2.0, 1.0),
        "brightness": _clamp_f(c.get("brightness", 1.0), 0.5, 2.0, 1.0),
        "invert": bool(c.get("invert", False)),
    }


def _resolve_font_path(char_set_key: str = "standard") -> str:
    if not _HAS_PIL:
        return ""
    with _CACHE_LOCK:
        if char_set_key in _FONT_PATH_CACHE:
            return _FONT_PATH_CACHE[char_set_key]

    cjk_candidates = [
        "NotoSansCJK-Regular.ttc",
        "NotoSansMonoCJKjp-Regular.otf",
        "DroidSansFallbackFull.ttf",
        "msgothic.ttc",
        "YuGothR.ttc",
    ]
    mono_candidates = [
        "DejaVuSansMono.ttf",
        "LiberationMono-Regular.ttf",
        "FreeMono.ttf",
        "Consolas.ttf",
        "cour.ttf",
    ]
    if char_set_key in FULLWIDTH_SETS:
        fc_pattern = "Noto Sans CJK JP"
        candidates = cjk_candidates + mono_candidates
    else:
        fc_pattern = "DejaVu Sans Mono"
        candidates = mono_candidates

    try:
        fc_out = subprocess.check_output(
            ["fc-match", "-f", "%{file}\n", fc_pattern],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.5,
        ).strip()
        if fc_out and os.path.isfile(fc_out):
            candidates = [fc_out] + candidates
    except Exception:
        pass

    chosen = ""
    for cand in candidates:
        try:
            ImageFont.truetype(cand, 16)
            chosen = cand
            break
        except Exception:
            continue

    with _CACHE_LOCK:
        _FONT_PATH_CACHE[char_set_key] = chosen
    return chosen


def get_font(size: int, char_set_key: str = "standard"):
    if not _HAS_PIL:
        return None
    sz = max(1, int(size))
    fpath = _resolve_font_path(char_set_key)
    key = (fpath, sz)
    with _CACHE_LOCK:
        cached = _FONT_CACHE.get(key)
        if cached is not None:
            return cached
    try:
        font = ImageFont.truetype(fpath, sz) if fpath else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    with _CACHE_LOCK:
        if len(_FONT_CACHE) > 64:
            _FONT_CACHE.clear()
        _FONT_CACHE[key] = font
    return font


def _order_cache_file() -> str:
    return os.path.join(_ascii_cache_dir(), "_char_order.json")


def sort_chars_by_density(chars: str, char_set_key: str = "standard") -> str:
    """Sort glyphs from darkest (least ink) to brightest and cache on disk."""
    if not _HAS_PIL:
        return chars
    fpath = _resolve_font_path(char_set_key)
    mem_key = (fpath, chars)
    with _CACHE_LOCK:
        hit = _SORTED_CHARS_CACHE.get(mem_key)
        if hit is not None:
            return hit

    disk_key = hashlib.sha256(f"{fpath}:{chars}".encode("utf-8")).hexdigest()[:24]
    order_path = _order_cache_file()
    disk_data = {}
    if os.path.isfile(order_path):
        try:
            with open(order_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                disk_data = loaded
                val = disk_data.get(disk_key)
                if isinstance(val, str) and len(val) >= 2:
                    with _CACHE_LOCK:
                        _SORTED_CHARS_CACHE[mem_key] = val
                    return val
        except Exception:
            disk_data = {}

    eval_font = get_font(24, char_set_key)
    unique_chars = list(dict.fromkeys(chars))
    canvas_size = 32
    scores = []
    for ch in unique_chars:
        if ch in (" ", "\u3000"):
            scores.append((0, ch))
            continue
        canvas = Image.new("L", (canvas_size, canvas_size), 0)
        d = ImageDraw.Draw(canvas)
        d.text((0, 0), ch, fill=255, font=eval_font)
        ink = sum(canvas.tobytes())
        if ink > 0:
            scores.append((ink, ch))

    scores.sort(key=lambda item: item[0])
    result = "".join(ch for _, ch in scores) or chars

    with _CACHE_LOCK:
        _SORTED_CHARS_CACHE[mem_key] = result
        try:
            os.makedirs(_ascii_cache_dir(), exist_ok=True)
            disk_data[disk_key] = result
            tmp_p = f"{order_path}.tmp.{os.getpid()}.{threading.get_ident()}"
            with open(tmp_p, "w", encoding="utf-8") as f:
                json.dump(disk_data, f)
            os.replace(tmp_p, order_path)
        except Exception:
            pass
    return result


def apply_color_mode(red: int, green: int, blue: int, luminance: float, mode: str) -> tuple[int, int, int]:
    if mode == "grayscale":
        v = max(0, min(255, int(round(luminance * 1.05))))
        return v, v, v
    if mode == "matrix":
        v = max(0, min(255, int(round(luminance * 1.15))))
        return int(v * 0.10), v, int(v * 0.22)
    if mode == "amber":
        v = max(0, min(255, int(round(luminance * 1.15))))
        return v, int(v * 0.70), int(v * 0.08)
    return red, green, blue


def _file_sig(path: str) -> tuple[str, int, int]:
    ap = os.path.abspath(os.path.expanduser(path))
    try:
        st = os.stat(ap)
        return ap, int(st.st_mtime_ns), int(st.st_size)
    except OSError:
        return ap, 0, 0


def image_cache_key(image_path: str, cfg: dict | None, preview: bool = False) -> str:
    c = normalize_cfg(cfg)
    ap, mtime_ns, size = _file_sig(image_path)
    items = tuple((k, c[k]) for k in sorted(c.keys()) if k != "enabled")
    payload = json.dumps([_RENDER_VERSION, ap, mtime_ns, size, items, bool(preview)], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_file_for(image_path: str, cfg: dict | None, preview: bool = False) -> str:
    prefix = "prev" if preview else "img"
    return os.path.join(_ascii_cache_dir(), f"{prefix}_{image_cache_key(image_path, cfg, preview=preview)}.png")


def get_cached_ascii_path(image_path: str, cfg: dict | None, preview: bool = False) -> str:
    if not image_path or not os.path.isfile(os.path.expanduser(image_path)):
        return ""
    out_p = _cache_file_for(image_path, cfg, preview=preview)
    return out_p if os.path.isfile(out_p) else ""


def _render_to_png(image_path: str, cfg: dict, out_path: str, preview: bool = False) -> str:
    if not _HAS_PIL:
        raise RuntimeError("Pillow is not installed")
    c = normalize_cfg(cfg)
    ap = os.path.abspath(os.path.expanduser(image_path))
    if not os.path.isfile(ap):
        raise FileNotFoundError(ap)

    if c["chars"] in CHAR_SETS:
        ascii_chars = CHAR_SETS[c["chars"]]
        char_set_key = c["chars"]
    else:
        ascii_chars = c["chars"]
        char_set_key = "custom"

    if char_set_key in FULLWIDTH_SETS:
        sorted_chars = sort_chars_by_density(ascii_chars, char_set_key)
        if len(sorted_chars) >= 2:
            ascii_chars = sorted_chars

    cell_aspect_ratio = 1.0 if char_set_key in FULLWIDTH_SETS else 0.5

    with Image.open(ap) as raw_img:
        orig_width, orig_height = raw_img.size
        if preview:
            raw_img.draft("RGB", (640, 640))
        img = raw_img.convert("RGB")

    if preview:
        max_dim = 640.0
        fit_scale = min(1.0, max_dim / float(max(orig_width, orig_height, 1)))
        out_width = max(16, int(round(orig_width * fit_scale * c["scale"])))
        out_height = max(16, int(round(orig_height * fit_scale * c["scale"])))
        if max(out_width, out_height) > int(max_dim):
            cap = max_dim / float(max(out_width, out_height))
            out_width = max(16, int(round(out_width * cap)))
            out_height = max(16, int(round(out_height * cap)))
        base_cell_height = (orig_height / BASE_TARGET_ROWS) * fit_scale
        base_cell_width = base_cell_height * cell_aspect_ratio
        rows = max(4, int(round((out_height / max(1e-3, base_cell_height)) * c["density"])))
        cols = max(4, int(round((out_width / max(1e-3, base_cell_width)) * c["density"])))
        max_rows = max(4, int(out_height / 4.0))
        if rows > max_rows:
            ratio = max_rows / float(rows)
            rows = max_rows
            cols = max(4, int(round(cols * ratio)))
        sampled_img = img.resize((cols, rows), Image.Resampling.LANCZOS)
        if c["brightness"] != 1.0:
            sampled_img = ImageEnhance.Brightness(sampled_img).enhance(c["brightness"])
        if c["contrast"] != 1.0:
            sampled_img = ImageEnhance.Contrast(sampled_img).enhance(c["contrast"])
        if c["invert"]:
            sampled_img = ImageOps.invert(sampled_img)
    else:
        if c["brightness"] != 1.0:
            img = ImageEnhance.Brightness(img).enhance(c["brightness"])
        if c["contrast"] != 1.0:
            img = ImageEnhance.Contrast(img).enhance(c["contrast"])
        if c["invert"]:
            img = ImageOps.invert(img)
        out_width = max(1, int(round(orig_width * c["scale"])))
        out_height = max(1, int(round(orig_height * c["scale"])))
        base_cell_height = orig_height / BASE_TARGET_ROWS
        base_cell_width = base_cell_height * cell_aspect_ratio
        rows = max(1, int(round((out_height / base_cell_height) * c["density"])))
        cols = max(1, int(round((out_width / base_cell_width) * c["density"])))
        sampled_img = img.resize((cols, rows), Image.Resampling.LANCZOS)

    cell_width = out_width / cols
    cell_height = out_height / rows
    font_size = max(1, int(round(cell_height * FONT_TO_HEIGHT_RATIO)))

    pixels = sampled_img.load()

    invert = c["invert"]
    tint = c["tint"]
    mode = c["mode"]
    is_mono = mode != "color"
    base_bg = (245, 245, 237) if invert else (10, 10, 18)
    num_chars = len(ascii_chars)
    last_idx = num_chars - 1

    grid_rows = []
    vis_sigs = [] if char_set_key == "binary" else None
    for r in range(rows):
        row_cells = []
        for c_idx in range(cols):
            raw_r, raw_g, raw_b = pixels[c_idx, r]
            luma = 0.299 * raw_r + 0.587 * raw_g + 0.114 * raw_b
            eff_lum = (0.65 * luma + 0.35 * max(raw_r, raw_g, raw_b)) if is_mono else luma
            sig = (255.0 - eff_lum) if invert else eff_lum
            row_cells.append((raw_r, raw_g, raw_b, eff_lum, sig))
            if vis_sigs is not None and sig >= 6.0:
                vis_sigs.append(sig)
        grid_rows.append(row_cells)

    if vis_sigs is not None:
        if vis_sigs:
            vis_sigs.sort()
            bin_p10 = vis_sigs[int(len(vis_sigs) * 0.10)]
            bin_p90 = vis_sigs[int(len(vis_sigs) * 0.90)]
        else:
            bin_p10, bin_p90 = 10.0, 200.0
        bin_span = max(12.0, bin_p90 - bin_p10)

    bg_pixels = [] if tint > 0 else None
    glyph_ops = []

    for r, row_cells in enumerate(grid_rows):
        y0 = r * cell_height
        bayer_row = _BAYER_2X2[r & 1]
        for c_idx, (raw_r, raw_g, raw_b, eff_lum, sig) in enumerate(row_cells):
            if char_set_key == "binary":
                if sig < 6.0:
                    ch = " "
                else:
                    norm = max(0.0, min(1.0, (sig - bin_p10) / bin_span))
                    ch = "0" if norm > bayer_row[c_idx & 1] else "1"
            else:
                char_index = min(last_idx, int((sig / 256.0) * num_chars))
                ch = ascii_chars[char_index]

            red, green, blue = apply_color_mode(raw_r, raw_g, raw_b, eff_lum, mode)

            if bg_pixels is not None:
                if invert:
                    bg_r = max(0, min(255, int(base_bg[0] - (255 - red) * tint)))
                    bg_g = max(0, min(255, int(base_bg[1] - (255 - green) * tint)))
                    bg_b = max(0, min(255, int(base_bg[2] - (255 - blue) * tint)))
                else:
                    bg_r = max(0, min(255, int(base_bg[0] + red * tint)))
                    bg_g = max(0, min(255, int(base_bg[1] + green * tint)))
                    bg_b = max(0, min(255, int(base_bg[2] + blue * tint)))
                bg_pixels.append((bg_r, bg_g, bg_b))

            if ch not in (" ", "\u3000"):
                glyph_ops.append((c_idx * cell_width, y0, ch, (red, green, blue)))

    if bg_pixels is not None:
        bg_grid = Image.new("RGB", (cols, rows))
        bg_grid.putdata(bg_pixels)
        out_img = bg_grid.resize((out_width, out_height), Image.Resampling.NEAREST)
    else:
        out_img = Image.new("RGB", (out_width, out_height), color=base_bg)

    draw = ImageDraw.Draw(out_img)
    font = get_font(font_size, char_set_key)
    for x0, y0, ch, fg in glyph_ops:
        draw.text((x0, y0), ch, fill=fg, font=font)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_path = f"{out_path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        out_img.save(tmp_path, format="PNG")
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return out_path


def render_ascii_image(image_path: str, cfg: dict | None, preview: bool = False) -> str:
    """Return cached or freshly rendered ASCIIfied PNG path."""
    c = normalize_cfg(cfg)
    out_p = _cache_file_for(image_path, c, preview=preview)
    if os.path.isfile(out_p):
        return out_p
    return _render_to_png(image_path, c, out_p, preview=preview)


def list_folder_media_for_slideshow(folder_path: str) -> tuple[list[str], list[str]]:
    """Return (static_images, gif_files) up to depth 2, matching core.wallpaper."""
    root = os.path.abspath(os.path.expanduser(str(folder_path or "").strip()))
    if not os.path.isdir(root):
        return [], []
    static_imgs, gifs = [], []
    for base, dirs, files in os.walk(root, followlinks=True):
        rel = os.path.relpath(base, root)
        depth = 0 if rel == "." else len(rel.split(os.sep))
        if depth >= 1:
            dirs[:] = []  # depth 1 subdirectories are files at maxdepth 2
        for fn in files:
            low = fn.lower()
            fp = os.path.join(base, fn)
            if low.endswith(ASCII_IMAGE_EXTS):
                static_imgs.append(fp)
            elif low.endswith(".gif"):
                gifs.append(fp)
    static_imgs.sort()
    gifs.sort()
    return static_imgs, gifs


def render_ascii_folder(
    folder_path: str,
    cfg: dict | None,
    progress_cb=None,
    cancel_check=None,
) -> tuple[str, int, int]:
    """Render all static images in a slideshow folder into a cached directory of symlinks."""
    c = normalize_cfg(cfg)
    root = os.path.abspath(os.path.expanduser(str(folder_path or "").strip()))
    static_imgs, gifs = list_folder_media_for_slideshow(root)
    if not static_imgs and not gifs:
        return "", 0, 0

    sigs = [_file_sig(f) for f in static_imgs + gifs]
    items = tuple((k, c[k]) for k in sorted(c.keys()) if k != "enabled")
    fhash = hashlib.sha256(
        json.dumps([_RENDER_VERSION, root, items, sigs], separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    out_dir = os.path.join(_ascii_cache_dir(), f"slide_{fhash}")
    os.makedirs(out_dir, exist_ok=True)

    expected = {}
    total_static = len(static_imgs)
    rendered_cnt = 0
    for idx, src_img in enumerate(static_imgs):
        if cancel_check and cancel_check():
            return "", rendered_cnt, total_static
        if progress_cb:
            progress_cb(idx + 1, total_static, os.path.basename(src_img))
        rendered_png = render_ascii_image(src_img, c, preview=False)
        stem = os.path.splitext(os.path.basename(src_img))[0]
        expected[f"{idx:04d}_{stem}.png"] = rendered_png
        rendered_cnt += 1

    for g_idx, gif_path in enumerate(gifs, start=total_static):
        expected[f"{g_idx:04d}_{os.path.basename(gif_path)}"] = gif_path

    # Populate/update deterministic symlinks and remove stale entries
    for name in os.listdir(out_dir):
        if name not in expected:
            try:
                os.unlink(os.path.join(out_dir, name))
            except OSError:
                pass
    for link_name, target_path in expected.items():
        link_path = os.path.join(out_dir, link_name)
        if os.path.lexists(link_path):
            try:
                if os.path.islink(link_path) and os.readlink(link_path) == target_path:
                    continue
                os.unlink(link_path)
            except OSError:
                pass
        os.symlink(target_path, link_path)

    return out_dir, rendered_cnt, total_static


def get_ascii_cache_stats() -> tuple[int, int]:
    """Return (png_file_count, total_bytes) in ASCII cache dir."""
    cdir = _ascii_cache_dir()
    if not os.path.isdir(cdir):
        return 0, 0
    cnt = 0
    total_bytes = 0
    try:
        with os.scandir(cdir) as it:
            for ent in it:
                if ent.name == "_char_order.json" or not ent.name.endswith(".png"):
                    continue
                if ent.is_file(follow_symlinks=False):
                    cnt += 1
                    try:
                        total_bytes += ent.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
    except OSError:
        pass
    return cnt, total_bytes


def clear_ascii_cache(keep_paths: set[str] | None = None) -> tuple[int, int]:
    """Delete cached PNGs and slide_* dirs, preserving live keep_paths and their targets."""
    cdir = _ascii_cache_dir()
    if not os.path.isdir(cdir):
        return 0, 0

    protected = set()
    for kp in keep_paths or ():
        if not kp:
            continue
        ap = os.path.abspath(os.path.expanduser(str(kp)))
        protected.add(ap)
        protected.add(os.path.realpath(ap))
        if os.path.isdir(ap):
            try:
                for fn in os.listdir(ap):
                    fp = os.path.join(ap, fn)
                    protected.add(fp)
                    protected.add(os.path.realpath(fp))
            except OSError:
                pass

    removed_entries = 0
    freed_bytes = 0
    try:
        with os.scandir(cdir) as it:
            for ent in it:
                if ent.name == "_char_order.json":
                    continue
                ep = os.path.abspath(ent.path)
                if ep in protected or os.path.realpath(ep) in protected:
                    continue
                if ent.is_dir(follow_symlinks=False) and ent.name.startswith("slide_"):
                    try:
                        shutil.rmtree(ep)
                        removed_entries += 1
                    except OSError:
                        pass
                elif ent.is_file(follow_symlinks=False) and (ent.name.endswith(".png") or ".png.tmp." in ent.name):
                    try:
                        sz = ent.stat(follow_symlinks=False).st_size
                        os.unlink(ep)
                        removed_entries += 1
                        freed_bytes += sz
                    except OSError:
                        pass
    except OSError:
        pass
    return removed_entries, freed_bytes
