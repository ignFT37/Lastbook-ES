"""Read-only address lookup using the state indexes installed with offline maps."""
import math
import re
import sqlite3
import unicodedata
from pathlib import Path

from engine.paths import ro_uri

# Keep normalization compatible with tools/build_addresses.py.
WORDS = {
    'boulevard': 'blvd', 'avenue': 'ave', 'av': 'ave', 'street': 'st',
    'drive': 'dr', 'road': 'rd', 'place': 'pl', 'court': 'ct', 'lane': 'ln',
    'terrace': 'ter', 'circle': 'cir', 'highway': 'hwy', 'parkway': 'pkwy',
    'square': 'sq', 'north': 'n', 'south': 's', 'east': 'e', 'west': 'w',
    'mount': 'mt', 'saint': 'st',
}
STATE_NAMES = (
    'Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|'
    'District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|'
    'Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|'
    'Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|'
    'New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|'
    'Pennsylvania|Puerto Rico|Rhode Island|South Carolina|South Dakota|'
    'Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming'
).split('|')
STATE_CODES = 'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA PR RI SC SD TN TX UT VT VA WA WV WI WY'.split()
STATES = dict(zip((name.lower() for name in STATE_NAMES), STATE_CODES))
ONES = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split()
ORDINALS = 'zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth'.split()
TO_DIGITS = {word: str(number) for number, word in enumerate(ONES)}
for number, word in enumerate(ORDINALS):
    suffix = 'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    TO_DIGITS[word] = f'{number}{suffix}'
TO_WORDS = {value: key for key, value in TO_DIGITS.items()}


def normalize(text):
    return ' '.join(WORDS.get(word, word) for word in re.sub(r'[^a-z0-9 ]+', ' ', text.lower()).split())


def is_address_query(query):
    """A standalone ZIP remains a place query. A number followed by words is an address."""
    return bool(re.match(r'^\s*\d[\w-]*\s+\S', query))


def street_forms(street):
    words = street.split()
    forms = [street, ' '.join(TO_DIGITS.get(word, word) for word in words),
             ' '.join(TO_WORDS.get(word, word) for word in words)]
    forms += [re.sub(r'\b(\d+)(st|nd|rd|th)\b', r'\1', form) for form in forms[:]]
    if words[0] not in ('n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'):
        forms += [f'{direction} {form}' for form in forms[:] for direction in ('n', 's', 'e', 'w')]
    return list(dict.fromkeys(forms))


def _parts(query):
    number, remainder = query.strip().split(None, 1)
    number = number.upper()
    remainder = re.sub(r'[^a-z0-9 ]+', ' ', remainder.lower()).split()
    # A ZIP+4 ("62704-1234", or "62704 1234" once the hyphen is gone) is the same place as its five-digit ZIP, which the
    # index keeps: drop the four extra digits so the ZIP and the state before it are still read.
    if len(remainder) > 1 and re.fullmatch(r'\d{4}', remainder[-1]) and re.fullmatch(r'\d{5}', remainder[-2]):
        remainder.pop()
    postcode = ''
    if remainder and re.fullmatch(r'\d{5}', remainder[-1]):
        postcode = remainder.pop()
    state = ''
    for count in (3, 2, 1):
        if len(remainder) <= count:
            continue
        name = ' '.join(remainder[-count:])
        code = STATES.get(name) or (name.upper() if count == 1 and name.upper() in STATE_CODES else '')
        if code:
            state = code
            remainder = remainder[:-count]
            break
    return number, normalize(' '.join(remainder)).split(), state, postcode


# ------------------------------------------------------------------ Spain ----
# A Spanish address reads "Calle de Alcalá 45, 28014 Madrid": street type, name, number, postcode, town. Overture's
# Spanish rows (scne.es) are capitals without accents, type first and articles moved to the end, cadastre style
# ("CALLE CUEVAS LAS"). Both sides go through es_street(), which tools/build_addresses_es.py also uses, so
# "Calle de las Cuevas", "C/ Cuevas" and "CALLE CUEVAS LAS" all meet on 'cuevas'.
ES_TYPES = {word: kind for kind, words in {
    'calle': 'calle c cl cll clle call carrer carrero rua r rue ruela rueiro kalea kale cai',
    'avenida': 'avenida av avd avda avgda avinguda etorbidea',
    'plaza': 'plaza pl pla plz plza pza pz placa praza plazuela placeta plazoleta enparantza',
    'paseo': 'paseo ps pso po pg passeig pasealekua ibilbidea',
    'carretera': 'carretera ctra crta cra cr ctr estrada errepidea autovia autopista',
    'camino': 'camino cm cno cmno cami camin bidea corredoira senda sendero vereda canada',
    'ronda': 'ronda rda rd',
    'travesia': 'travesia trv trva trav tr travessera travessia travesa zeharkalea',
    'glorieta': 'glorieta gta glta rotonda',
    'callejon': 'callejon cjon cj kalezuloa',
    'pasaje': 'pasaje pje psje pj passatge ptge pasadizo',
    'cuesta': 'cuesta cta costanilla bajada baixada subida pujada aldapa',
    'rambla': 'rambla rbla rb',
    'urbanizacion': 'urbanizacion urb ur',
    'barrio': 'barrio bo barri barriada bda auzoa',
    'poligono': 'poligono pol polig poligon',
    'lugar': 'lugar lg',
    'diseminado': 'diseminado diseminados ds dis',
    'via': 'via',
    'bulevar': 'bulevar boulevard',
    'alameda': 'alameda alda',
    'parque': 'parque pq parc',
    'partida': 'partida pda',
    'carrera': 'carrera',
}.items() for word in words.split()}
ES_TYPES_AFTER = {word: ES_TYPES.get(word, 'calle') for word in
                  'kalea kale etorbidea enparantza pasealekua ibilbidea bidea errepidea zeharkalea kalezuloa aldapa auzoa'.split()}
ES_STOP = set('de del la las el los l d al dels els les lo sa ses es des na en o a os as da do das dos y i'.split())
ES_ABBR = {'sta': 'santa', 'sto': 'santo', 'sr': 'senor', 'sra': 'senora', 'ntra': 'nuestra', 'ntro': 'nuestro',
           'nra': 'nuestra', 'gral': 'general', 'dr': 'doctor', 'dra': 'doctora', 'hnos': 'hermanos', 'pdte': 'presidente',
           'prof': 'profesor', 'ing': 'ingeniero', 'mtro': 'maestro', 'cap': 'capitan', 'cte': 'comandante',
           'tte': 'teniente', 'sgto': 'sargento', 'fco': 'francisco', 'cdad': 'ciudad'}
ES_FLOOR = set('piso planta pta puerta izq izqda izda izquierda dcha dch der drcha derecha bajo bj bajos atico entlo '
               'entresuelo entreplanta esc escalera portal bloque blq bl local loc apto apartamento ext exterior int '
               'interior'.split())
ES_COUNTRY = {'espana', 'spain'}
# INE province code (the first two digits of every Spanish postcode) -> (province, community code)
_ES_PROVINCE_ROWS = (
    ('01', 'Araba/Álava', 'PV'), ('02', 'Albacete', 'CM'), ('03', 'Alicante/Alacant', 'VC'), ('04', 'Almería', 'AN'),
    ('05', 'Ávila', 'CL'), ('06', 'Badajoz', 'EX'), ('07', 'Illes Balears', 'IB'), ('08', 'Barcelona', 'CT'),
    ('09', 'Burgos', 'CL'), ('10', 'Cáceres', 'EX'), ('11', 'Cádiz', 'AN'), ('12', 'Castellón/Castelló', 'VC'),
    ('13', 'Ciudad Real', 'CM'), ('14', 'Córdoba', 'AN'), ('15', 'A Coruña', 'GA'), ('16', 'Cuenca', 'CM'),
    ('17', 'Girona', 'CT'), ('18', 'Granada', 'AN'), ('19', 'Guadalajara', 'CM'), ('20', 'Gipuzkoa', 'PV'),
    ('21', 'Huelva', 'AN'), ('22', 'Huesca', 'AR'), ('23', 'Jaén', 'AN'), ('24', 'León', 'CL'), ('25', 'Lleida', 'CT'),
    ('26', 'La Rioja', 'RI'), ('27', 'Lugo', 'GA'), ('28', 'Madrid', 'MD'), ('29', 'Málaga', 'AN'), ('30', 'Murcia', 'MC'),
    ('31', 'Navarra', 'NC'), ('32', 'Ourense', 'GA'), ('33', 'Asturias', 'AS'), ('34', 'Palencia', 'CL'),
    ('35', 'Las Palmas', 'CN'), ('36', 'Pontevedra', 'GA'), ('37', 'Salamanca', 'CL'), ('38', 'Santa Cruz de Tenerife', 'CN'),
    ('39', 'Cantabria', 'CB'), ('40', 'Segovia', 'CL'), ('41', 'Sevilla', 'AN'), ('42', 'Soria', 'CL'),
    ('43', 'Tarragona', 'CT'), ('44', 'Teruel', 'AR'), ('45', 'Toledo', 'CM'), ('46', 'Valencia/València', 'VC'),
    ('47', 'Valladolid', 'CL'), ('48', 'Bizkaia', 'PV'), ('49', 'Zamora', 'CL'), ('50', 'Zaragoza', 'AR'),
    ('51', 'Ceuta', 'CE'), ('52', 'Melilla', 'ML'))
ES_PROVINCES = {code: (name, region) for code, name, region in _ES_PROVINCE_ROWS}
ES_REGIONS = {
    'AN': 'Andalucía', 'AR': 'Aragón', 'AS': 'Asturias', 'IB': 'Illes Balears', 'CN': 'Canarias', 'CB': 'Cantabria',
    'CL': 'Castilla y León', 'CM': 'Castilla-La Mancha', 'CT': 'Cataluña', 'VC': 'Comunitat Valenciana',
    'EX': 'Extremadura', 'GA': 'Galicia', 'MD': 'Comunidad de Madrid', 'MC': 'Región de Murcia', 'NC': 'Navarra',
    'PV': 'País Vasco', 'RI': 'La Rioja', 'CE': 'Ceuta', 'ML': 'Melilla'}
ROMAN = re.compile(r'M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})')


def es_fold(text):
    """Lower case without accents: "Plaça de l'Àngel" -> "placa de l'angel"; ñ -> n, ç -> c, l·l -> ll."""
    text = unicodedata.normalize('NFKD', str(text).replace('·', '').replace('ŀ', 'l').replace('Ŀ', 'L'))
    return ''.join(ch for ch in text if not unicodedata.combining(ch)).lower()


def es_words(text):
    return [ES_ABBR.get(word, word) for word in re.sub(r'[^a-z0-9]+', ' ', es_fold(text)).split()]


def es_street(text):
    """(type, key) for a street name: ('calle', 'cuevas') for "Calle de las Cuevas", "C/ Cuevas" and "CALLE CUEVAS LAS"."""
    words = es_words(text)
    words = [word for word in words if word not in ES_STOP] or words
    kind = ''
    for _ in range(2):  # "CALLE KALE ZAHARRA": Overture puts a Spanish type before a Basque one
        if len(words) > 1 and words[0] in ES_TYPES:
            kind = kind or ES_TYPES[words[0]]
            words = words[1:]
    if len(words) > 1 and words[-1] in ES_TYPES_AFTER:  # Basque order: "Nagusia kalea"
        kind = kind or ES_TYPES_AFTER[words[-1]]
        words = words[:-1]
    return kind, ' '.join(words)


def es_region(postcode):
    """(province, community code) from a five-digit postcode, or ('', '')."""
    return ES_PROVINCES.get(str(postcode or '')[:2], ('', ''))


def es_display_street(street):
    """'CALLE CUEVAS LAS' -> 'Calle las Cuevas'; 'AVENIDA ALFONSO XII' -> 'Avenida Alfonso XII'."""
    words = street.split()
    if len(words) > 2 and words[-1].lower() in ('el', 'la', 'los', 'las'):
        words = [words[0], words[-1]] + words[1:-1]
    out = []
    for i, word in enumerate(words):
        low = word.lower()
        if i and low in ES_STOP:
            out.append(low)
        elif len(word) <= 5 and word.isupper() and ROMAN.fullmatch(word):
            out.append(word)
        else:
            out.append('-'.join(part[:1].upper() + part[1:].lower() for part in word.split('-')))
    return ' '.join(out)


def _es_parts(query):
    """(number, letter, street, postcode, town words) for a Spanish-style address, or None.

    "Calle de Alcalá 45, 28014 Madrid", "C/ Alcalá, 45, 3º izq.", "Avda. Diagonal 640 Barcelona", "Rúa Nova s/n" and
    "45 Calle Alcalá, Madrid" all parse; floor and door after the number are dropped."""
    text = re.sub(r'(?i)\bc\s*/', ' calle ', query)
    text = re.sub(r'(?i)\bs\s*/\s*n\b', ' sn ', text)
    text = re.sub(r'(?i)\bn(?:[.\s]*[º°o])?[.\s]*(?=\d)', ' ', text)  # "nº 45", "n.º 45"
    tokens = re.findall(r'\d+[a-z]?|[a-z]+|,', es_fold(text))
    index = None
    for i, token in enumerate(tokens):
        if re.fullmatch(r'\d{1,4}[a-z]?', token) or token == 'sn':
            if i == 0 and len(tokens) > 1 and re.fullmatch(r'[a-z]+', tokens[1]):
                index = i
                break
            if any(re.fullmatch(r'[a-z]+', t) and t != 'sn' for t in tokens[:i]):
                index = i
                break
    if index is None:
        return None
    if index == 0:  # "45 Calle Alcalá, Madrid": the street runs to the first comma or postcode
        rest = tokens[1:]
        end = next((k for k, t in enumerate(rest) if t == ',' or re.fullmatch(r'\d{5}', t)), len(rest))
        street, after = rest[:end], rest[end:]
    else:
        street, after = tokens[:index], tokens[index + 1:]
    street = [t for t in street if re.fullmatch(r'[a-z]+|\d+[a-z]?', t)]
    match = re.fullmatch(r'(\d{1,4})([a-z]?)', tokens[index])
    number, letter = (match[1], match[2]) if match else ('S-N', '')
    if not letter and after and re.fullmatch(r'[a-h]', after[0]):
        letter, after = after[0], after[1:]
    postcode = next((t for t in after if re.fullmatch(r'(0[1-9]|[1-4]\d|5[0-2])\d{3}', t)), '')
    town = [t for t in after if t != ',' and t != postcode and t not in ES_FLOOR and t not in ES_COUNTRY
            and t not in ES_STOP and len(t) > 1 and not re.fullmatch(r'\d+[a-z]?', t)]
    if not any(re.fullmatch(r'[a-z]+', t) for t in street):
        return None
    return number, letter.upper(), ' '.join(street), postcode, town


def is_es_address_query(query):
    return _es_parts(query) is not None


def find_es_addresses(query, geo_directory, limit=8, near=None):
    """Exact house numbers from the Spanish index (addresses/ES.sqlite, tools/build_addresses_es.py), never a guess."""
    parts = _es_parts(query)
    paths = sorted((Path(geo_directory) / 'addresses').glob('ES*.sqlite'))
    if not parts or not paths:
        return []
    number, letter, street, postcode, town = parts
    kind, key = es_street(street)
    if not key:
        return []
    rows = []
    for path in paths:
        try:
            with sqlite3.connect(ro_uri(path), uri=True) as connection:
                rows += connection.execute(
                    'SELECT num,unit,street,kind,city,cnorm,state,zip,lat,lon,norm FROM addr '
                    'WHERE norm >= ? AND norm < ? AND num = ? ORDER BY length(norm) LIMIT 400',
                    (key, key + '￿', number)).fetchall()
        except sqlite3.Error:
            continue
    if not rows:
        return []

    def rank(row):
        num, unit, name, row_kind, city, cnorm, state, zip_code, lat, lon, norm = row
        province = es_fold(es_region(zip_code)[0])
        here = set(cnorm.split()) | set(re.findall(r'[a-z]+', province)) | set(es_words(ES_REGIONS.get(state, '')))
        town_hits = sum(word in here for word in town)
        miss = (norm != key) * 4 + (bool(kind) and row_kind != kind) * 1 + (bool(postcode) and zip_code != postcode) * 3
        miss += (unit != letter) * (1 if letter else 0.5) + (len(town) - town_hits) * 2
        distance = 0
        if near:
            distance = (lat - near[0]) ** 2 + ((lon - near[1]) * math.cos(math.radians(near[0]))) ** 2
        return miss, distance, len(norm)

    rows.sort(key=rank)
    best = rank(rows[0])[0]
    results, seen = [], set()
    for row in rows:
        num, unit, name, row_kind, city, cnorm, state, zip_code, lat, lon, norm = row
        if rank(row)[0] > best + 2:  # the same street name in another town, when the postcode or town given matched
            break
        if (num, unit, norm, zip_code) in seen:
            continue
        seen.add((num, unit, norm, zip_code))
        place = ' '.join(part for part in (zip_code, city) if part)
        results.append({
            'name': f'{es_display_street(name)} {num if num != "S-N" else "s/n"}{unit}', 'kind': 'Street address',
            'detail': ', '.join(part for part in (place, ES_REGIONS.get(state, '')) if part),
            'lat': lat, 'lon': lon, 'source': 'Overture Maps (scne.es)', 'onMap': True,
        })
        if len(results) == limit:
            break
    return results


def find_addresses(query, geo_directory, limit=8, near=None):
    """Return exact house-number matches from installed indexes, never a guessed house."""
    spanish = find_es_addresses(query, geo_directory, limit=limit, near=near)
    if spanish:
        return spanish
    if not is_address_query(query):
        return []
    number, words, state, postcode = _parts(query)
    if not words:
        return []
    geo_directory = Path(geo_directory)
    paths = [path for path in sorted((geo_directory / 'addresses').glob('*.sqlite')) if not path.stem.upper().startswith('ES')]
    if state:
        paths = [path for path in paths if path.stem.upper() == state]
    legacy = geo_directory / 'addresses.sqlite'
    if not paths and legacy.is_file():
        paths = [legacy]
    connections = []
    try:
        for path in paths:
            try:
                connections.append(sqlite3.connect(ro_uri(path), uri=True))
            except sqlite3.Error:
                continue
        for count in range(len(words), 0, -1):
            street = ' '.join(words[:count])
            if all(word in set(WORDS.values()) for word in words[:count]):
                continue
            city = ' '.join(words[count:])
            rows = []
            for form in street_forms(street):
                conditions = ['num IN (?, ?)', 'norm >= ?', 'norm < ?']
                parameters = [number, number.lower(), form, form + '\uffff']
                if state:
                    conditions.append('state = ?')
                    parameters.append(state)
                if postcode:
                    conditions.append('zip = ?')
                    parameters.append(postcode)
                if city:
                    conditions.append('lower(city) LIKE ?')
                    parameters.append(city + '%')
                sql = ('SELECT num,street,city,state,zip,lat,lon,norm FROM addr WHERE '
                       + ' AND '.join(conditions) + ' ORDER BY length(norm),norm LIMIT ?')
                for connection in connections:
                    try:
                        rows.extend(connection.execute(sql, [*parameters, limit * 3]).fetchall())
                    except sqlite3.Error:
                        continue
            if rows:
                def rank(row):
                    distance = 0
                    if near:
                        distance = (row[5] - near[0]) ** 2 + ((row[6] - near[1]) * math.cos(math.radians(near[0]))) ** 2
                    return len(row[7]), distance, row[3], row[2], row[1]
                results, seen = [], set()
                for num, name, city_name, state_code, zip_code, lat, lon, normalized in sorted(rows, key=rank):
                    key = (num, normalized, lat, lon)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append({
                        'name': f'{num} {name}', 'kind': 'Street address',
                        'detail': ', '.join(part for part in (city_name, state_code, zip_code) if part),
                        'lat': lat, 'lon': lon, 'source': 'Overture Maps', 'onMap': True,
                    })
                    if len(results) == limit:
                        break
                return results
        return []
    finally:
        for connection in connections:
            connection.close()
